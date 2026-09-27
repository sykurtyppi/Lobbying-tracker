"""
Entity aggregation, filtered leaderboard, YoY growth, enriched leaderboard.
"""

import sqlite3
from datetime import datetime

import pandas as pd
import streamlit as st


def aggregate_entities(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse raw company rows into one row per investable entity.
    Uses ticker when available; falls back to exact company name.
    """
    if df.empty:
        return df

    work = df.copy()
    work["company_name"] = work["company_name"].fillna("").astype(str).str.strip()
    work["ticker"] = (
        work["ticker"].fillna("").astype(str).str.strip().str.upper()
    )
    work["quarter"] = work["quarter"].fillna("").astype(str).str.strip()
    work["entity_key"] = work["ticker"].where(work["ticker"] != "", work["company_name"])
    work = work[work["entity_key"] != ""].copy()

    work["total_lobbying_spend"] = pd.to_numeric(
        work["total_lobbying_spend"], errors="coerce"
    ).fillna(0.0)
    work["market_cap"] = pd.to_numeric(work["market_cap"], errors="coerce")
    work["year"] = pd.to_numeric(work["year"], errors="coerce")

    representative = (
        work.assign(mcap_sort=work["market_cap"].fillna(-1))
        .sort_values(
            ["entity_key", "total_lobbying_spend", "mcap_sort", "company_name"],
            ascending=[True, False, False, True],
        )
        .drop_duplicates("entity_key", keep="first")
        .set_index("entity_key")
    )

    q_order = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}

    grouped = work.groupby("entity_key", dropna=False).agg(
        total_lobbying_spend=("total_lobbying_spend", "sum"),
        market_cap=("market_cap", "max"),
        year=("year", "max"),
        quarters_covered=(
            "quarter",
            lambda s: ",".join(
                sorted(
                    {q for q in s if q},
                    key=lambda q: (q_order.get(q, 99), q),
                )
            ),
        ),
        source_rows=("entity_key", "size"),
    )

    sector_choice = (
        work.assign(sector_clean=work["sector"].fillna("").astype(str).str.strip())
        .loc[lambda d: d["sector_clean"] != ""]
        .groupby(["entity_key", "sector_clean"], as_index=False)["total_lobbying_spend"]
        .sum()
        .sort_values(
            ["entity_key", "total_lobbying_spend", "sector_clean"],
            ascending=[True, False, True],
        )
        .drop_duplicates("entity_key")
        .set_index("entity_key")["sector_clean"]
    )

    grouped["company_name"] = representative["company_name"]
    grouped["ticker"] = representative["ticker"].replace("", pd.NA)
    grouped["quarter"] = grouped["quarters_covered"]
    grouped["sector"] = sector_choice
    grouped["year"] = pd.to_numeric(grouped["year"], errors="coerce").round().astype("Int64")
    grouped["spend_to_mcap_ratio"] = grouped.apply(
        lambda row: (
            row["total_lobbying_spend"] / row["market_cap"]
            if pd.notna(row["market_cap"]) and row["market_cap"] > 0
            else pd.NA
        ),
        axis=1,
    )

    grouped = grouped.reset_index()
    return grouped[
        [
            "entity_key",
            "company_name",
            "ticker",
            "year",
            "quarter",
            "total_lobbying_spend",
            "market_cap",
            "spend_to_mcap_ratio",
            "sector",
            "source_rows",
            "quarters_covered",
        ]
    ]


def get_filtered_data(fetcher, year, quarter, market_cap_filters, sector_filters, min_spend, limit=None):
    """Get filtered lobbying data from database"""
    import sqlite3
    
    conn = sqlite3.connect(fetcher.db_path)
    
    # Build query based on filters
    query = """
        SELECT 
            company_name,
            ticker,
            year,
            quarter,
            total_lobbying_spend,
            market_cap,
            spend_to_mcap_ratio,
            sector
        FROM company_lobbying
        WHERE 1=1
          AND total_lobbying_spend > 0
    """
    
    params = []
    
    # Year/quarter filter
    if quarter == 'YTD':
        # Year-to-date for current year; otherwise all available quarters in selected year.
        query += " AND year = ?"
        params.append(year)
        current_year = datetime.now().year
        if year == current_year:
            current_q_num = (datetime.now().month - 1) // 3 + 1
            allowed_quarters = ["Q1", "Q2", "Q3", "Q4"][:current_q_num]
            placeholders = ",".join(["?" for _ in allowed_quarters])
            query += f" AND quarter IN ({placeholders})"
            params.extend(allowed_quarters)
    elif quarter != 'Full Year':
        query += " AND year = ? AND quarter = ?"
        params.extend([year, quarter])
    else:
        query += " AND year = ?"
        params.append(year)

    try:
        df = pd.read_sql_query(query, conn, params=params)
    except Exception as e:
        st.error(f"Database query error: {e}")
        st.code(query)
        st.write("Params:", params)
        df = pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return df

    # Aggregate to one row per entity first, then apply filters.
    df = aggregate_entities(df)

    # Min spend filter (applied on aggregated totals)
    if min_spend > 0:
        df = df[df["total_lobbying_spend"] >= (min_spend * 1_000_000)]

    # Market cap filters (applied on aggregated entity market cap)
    if market_cap_filters and len(market_cap_filters) > 0:
        cap_mask = pd.Series(False, index=df.index)
        if 'Large Cap (>$10B)' in market_cap_filters:
            cap_mask |= df["market_cap"] >= 10_000_000_000
        if 'Mid Cap ($2B-$10B)' in market_cap_filters:
            cap_mask |= (df["market_cap"] >= 2_000_000_000) & (df["market_cap"] < 10_000_000_000)
        if 'Small Cap (<$2B)' in market_cap_filters:
            cap_mask |= df["market_cap"] < 2_000_000_000
        df = df[cap_mask]

    # Sector filter
    if sector_filters and len(sector_filters) > 0:
        df = df[df["sector"].isin(sector_filters)]

    df = df.sort_values("total_lobbying_spend", ascending=False).reset_index(drop=True)
    if limit is not None:
        return df.head(int(limit)).reset_index(drop=True)
    return df


@st.cache_data
def get_yearly_summary(db_path, refresh_token=0):
    """Get yearly rollups used for historical coverage charts."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                year,
                COUNT(*) AS total_rows,
                COUNT(DISTINCT company_name) AS unique_companies,
                COUNT(
                    DISTINCT CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE TRIM(company_name)
                    END
                ) AS unique_entities,
                SUM(total_lobbying_spend) AS total_lobbying_spend,
                SUM(CASE WHEN ticker IS NOT NULL AND ticker != '' THEN 1 ELSE 0 END) AS mapped_rows,
                COUNT(
                    DISTINCT CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE NULL
                    END
                ) AS mapped_entities,
                SUM(CASE WHEN market_cap IS NOT NULL AND market_cap > 0 THEN 1 ELSE 0 END) AS with_market_cap_rows,
                COUNT(
                    DISTINCT CASE
                        WHEN market_cap IS NOT NULL AND market_cap > 0 THEN
                            CASE
                                WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                                ELSE TRIM(company_name)
                            END
                        ELSE NULL
                    END
                ) AS with_market_cap_entities
            FROM company_lobbying
            WHERE total_lobbying_spend > 0
            GROUP BY year
            ORDER BY year
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return df

    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df = df.dropna(subset=["year"]).copy()
    df["year"] = df["year"].astype(int)
    df["ticker_match_rate"] = (df["mapped_rows"] / df["total_rows"]) * 100
    df["market_cap_coverage"] = (df["with_market_cap_rows"] / df["total_rows"]) * 100
    df["entity_match_rate"] = (df["mapped_entities"] / df["unique_entities"]) * 100
    df["entity_mcap_coverage"] = (
        df["with_market_cap_entities"] / df["unique_entities"]
    ) * 100
    return df


@st.cache_data
def get_yoy_growth_leaders(db_path, current_year, refresh_token=0, top_n=5):
    """
    Calculate YoY lobbying growth leaders from the investable universe.

    Universe:
      - ticker-mapped entities only
      - current-year annual spend >= $1M
      - prior-year annual spend >= $250K (avoids tiny-base distortions)

    Returns one row per ticker (deduped at entity level).
    """
    import sqlite3

    prev_year = current_year - 1
    min_curr_spend = 1_000_000
    min_prev_spend = 250_000
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            WITH curr AS (
                SELECT ticker, SUM(total_lobbying_spend) AS curr_spend
                FROM company_lobbying
                WHERE year = :curr_year
                  AND ticker IS NOT NULL
                  AND ticker != ''
                GROUP BY ticker
            ),
            prev AS (
                SELECT ticker, SUM(total_lobbying_spend) AS prev_spend
                FROM company_lobbying
                WHERE year = :prev_year
                  AND ticker IS NOT NULL
                  AND ticker != ''
                GROUP BY ticker
            ),
            rep_name AS (
                SELECT ticker, company_name
                FROM (
                    SELECT
                        ticker,
                        company_name,
                        SUM(total_lobbying_spend) AS spend,
                        ROW_NUMBER() OVER (
                            PARTITION BY ticker
                            ORDER BY SUM(total_lobbying_spend) DESC, company_name ASC
                        ) AS rn
                    FROM company_lobbying
                    WHERE year = :curr_year
                      AND ticker IS NOT NULL
                      AND ticker != ''
                    GROUP BY ticker, company_name
                )
                WHERE rn = 1
            )
            SELECT
                rn.company_name,
                c.ticker,
                c.curr_spend,
                p.prev_spend,
                ROUND((c.curr_spend - p.prev_spend) * 100.0 / p.prev_spend, 1) AS yoy_pct
            FROM curr c
            JOIN prev p ON c.ticker = p.ticker
            JOIN rep_name rn ON rn.ticker = c.ticker
            WHERE c.curr_spend >= :min_curr
              AND p.prev_spend >= :min_prev
            ORDER BY yoy_pct DESC, c.curr_spend DESC
            LIMIT :top_n
            """,
            conn,
            params={
                "curr_year": current_year,
                "prev_year": prev_year,
                "min_curr": min_curr_spend,
                "min_prev": min_prev_spend,
                "top_n": int(top_n),
            },
        )
    finally:
        conn.close()

    return df


@st.cache_data
def get_enriched_leaderboard(db_path, year, refresh_token=0):
    """
    Returns enrichment columns for the leaderboard keyed by entity_key
    (ticker when available, else company_name):
      yoy_pct          – year-over-year % change in annual lobbying spend
      qoq_pct          – quarter-over-quarter % change (latest vs prior quarter)
      is_spike         – bool: YoY > 50% AND spend >= $1M AND above sector median
      is_consistent    – bool: 3+ consecutive increasing quarters in recent window
      accel_vs_sector  – company YoY% minus sector median YoY%

    Merge the result onto filtered_df using company_name.
    """
    import sqlite3

    prev_year = year - 1
    conn = sqlite3.connect(db_path)
    try:
        # Annual spend per entity for current + prior year
        annual_df = pd.read_sql_query(
            """
            SELECT
                CASE
                    WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                    ELSE TRIM(company_name)
                END AS entity_key,
                year,
                SUM(total_lobbying_spend) AS annual_spend
            FROM company_lobbying
            WHERE year IN (?, ?)
            GROUP BY entity_key, year
            """,
            conn,
            params=(year, prev_year),
        )

        # Quarterly spend over a 3-year window for QoQ and consistency
        quarterly_df = pd.read_sql_query(
            """
            SELECT
                CASE
                    WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                    ELSE TRIM(company_name)
                END AS entity_key,
                year,
                quarter,
                SUM(total_lobbying_spend) AS q_spend
            FROM company_lobbying
            WHERE year >= ? AND year <= ?
            GROUP BY entity_key, year, quarter
            """,
            conn,
            params=(year - 2, year),
        )

        # Primary sector for each entity in the current year
        sector_df = pd.read_sql_query(
            """
            WITH sector_spend AS (
                SELECT
                    CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE TRIM(company_name)
                    END AS entity_key,
                    sector,
                    SUM(total_lobbying_spend) AS sector_spend
                FROM company_lobbying
                WHERE year = ?
                  AND sector IS NOT NULL
                  AND TRIM(sector) != ''
                GROUP BY entity_key, sector
            ),
            ranked AS (
                SELECT
                    entity_key,
                    sector,
                    ROW_NUMBER() OVER (
                        PARTITION BY entity_key
                        ORDER BY sector_spend DESC, sector ASC
                    ) AS rn
                FROM sector_spend
            )
            SELECT entity_key, sector
            FROM ranked
            WHERE rn = 1
            """,
            conn,
            params=(year,),
        )
    finally:
        conn.close()

    if annual_df.empty:
        return pd.DataFrame()

    # ── YoY% ─────────────────────────────────────────────────────────────────
    curr_annual = (
        annual_df[annual_df["year"] == year]
        .set_index("entity_key")["annual_spend"]
    )
    prev_annual = (
        annual_df[annual_df["year"] == prev_year]
        .set_index("entity_key")["annual_spend"]
    )
    common = curr_annual.index
    yoy_pct = pd.Series(index=common, dtype=float, name="yoy_pct")
    for entity_key in common:
        c = curr_annual.get(entity_key)
        p = prev_annual.get(entity_key)
        if p is None or p <= 0:
            yoy_pct[entity_key] = None
        else:
            yoy_pct[entity_key] = round((c - p) / p * 100, 1)

    # ── QoQ% (latest available quarter vs the one before it) ─────────────────
    q_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    qt = quarterly_df.copy()
    qt["q_num"] = qt["quarter"].map(q_map)
    qt = qt.dropna(subset=["q_num"])
    qt["period"] = qt["year"] * 4 + qt["q_num"]
    qt = qt.sort_values(["entity_key", "period"])

    def _qoq(grp):
        grp = grp.sort_values("period")
        if len(grp) < 2:
            return None
        latest = float(grp.iloc[-1]["q_spend"])
        prior = float(grp.iloc[-2]["q_spend"])
        if prior <= 0:
            return None
        return round((latest - prior) / prior * 100, 1)

    qoq_series = qt.groupby("entity_key").apply(_qoq)

    # ── Consistency flag (3 or more consecutive quarters of increasing spend) ─
    def _consistent(grp):
        grp = grp.sort_values("period")
        spends = grp["q_spend"].values
        if len(spends) < 3:
            return False
        for i in range(len(spends) - 2):
            if spends[i] < spends[i + 1] < spends[i + 2]:
                return True
        return False

    consistency_series = qt.groupby("entity_key").apply(_consistent)

    # ── Build result frame ────────────────────────────────────────────────────
    result = pd.DataFrame({"yoy_pct": yoy_pct, "curr_spend": curr_annual})
    result = result.reset_index().rename(columns={"index": "entity_key"})
    result["qoq_pct"] = result["entity_key"].map(qoq_series)
    result["is_consistent"] = result["entity_key"].map(consistency_series).fillna(False)

    sector_map = (
        sector_df.drop_duplicates("entity_key")
        .set_index("entity_key")["sector"]
    )
    result["sector"] = result["entity_key"].map(sector_map)

    # ── Sector median YoY% → Acceleration vs Sector ──────────────────────────
    sec_med_yoy = result.groupby("sector")["yoy_pct"].median()
    result["sector_median_yoy"] = result["sector"].map(sec_med_yoy)
    result["accel_vs_sector"] = (result["yoy_pct"] - result["sector_median_yoy"]).round(1)

    # ── Spike flag ────────────────────────────────────────────────────────────
    sec_med_spend = result.groupby("sector")["curr_spend"].median()
    result["sector_median_spend"] = result["sector"].map(sec_med_spend)
    result["is_spike"] = (
        (result["yoy_pct"].fillna(0) > 50)
        & (result["curr_spend"] >= 1_000_000)
        & (result["curr_spend"] > result["sector_median_spend"].fillna(0))
    )

    return result[[
        "entity_key", "yoy_pct", "qoq_pct",
        "is_spike", "is_consistent", "accel_vs_sector",
    ]]
