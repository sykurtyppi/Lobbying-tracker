"""
Conviction scoring of lobbying entities and its benchmark backtest.
"""

import pandas as pd
import streamlit as st



@st.cache_data
def get_conviction_scores(
    db_path, year, refresh_token=0, min_spend_m=1.0, ticker_only=True
):
    """
    Compute a Conviction Score (0-100) for each company in `year`.

    Components
    ----------
    YoY Growth    (0-40 pts)  – concave (sqrt) curve; reaches max at 500% growth.
                               Decline bands (negative YoY) score 0–8 pts.
                               New entrants with no prior-year data get 15 pts (neutral).
    Spend/MCap    (0-25 pts)  – percentile rank of spend-to-market-cap ratio within
                               the scored universe.  Companies without market-cap data
                               score 0 (not the old 10-pt free bonus).
    Consistency   (0-25 pts)  – proportion of the year's 4 quarters with filings.
    OpenSecrets   (0-10 pts)  – percentile rank of combined PAC + individual
                               contributions for the year.  Scores 0 when the
                               opensecrets_contribs table is absent or empty.

    Maximum possible score: 100 (with OS data) | 90 (without OS data).

    Parameters
    ----------
    ticker_only : bool (default True)
        When True, only companies that have been mapped to a public stock ticker
        are included.  This keeps the investable universe clean and prevents
        private companies, universities, and non-profits from crowding the top.
    """
    import sqlite3

    prev_year = year - 1
    min_spend = min_spend_m * 1_000_000

    conn = sqlite3.connect(db_path)
    try:
        if ticker_only:
            df = pd.read_sql_query(
                """
                WITH curr_by_ticker AS (
                    SELECT
                        ticker,
                        SUM(total_lobbying_spend) AS total_spend,
                        MAX(market_cap)           AS market_cap,
                        COUNT(DISTINCT quarter)   AS quarters_count
                    FROM company_lobbying
                    WHERE year = :curr_year
                      AND ticker IS NOT NULL
                      AND ticker != ''
                    GROUP BY ticker
                    HAVING total_spend >= :min_spend
                ),
                curr_name AS (
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
                ),
                curr_sector AS (
                    SELECT ticker, sector
                    FROM (
                        SELECT
                            ticker,
                            sector,
                            SUM(total_lobbying_spend) AS spend,
                            ROW_NUMBER() OVER (
                                PARTITION BY ticker
                                ORDER BY SUM(total_lobbying_spend) DESC, sector ASC
                            ) AS rn
                        FROM company_lobbying
                        WHERE year = :curr_year
                          AND ticker IS NOT NULL
                          AND ticker != ''
                          AND sector IS NOT NULL
                          AND TRIM(sector) != ''
                        GROUP BY ticker, sector
                    )
                    WHERE rn = 1
                ),
                prev_by_ticker AS (
                    SELECT ticker, SUM(total_lobbying_spend) AS total_spend
                    FROM company_lobbying
                    WHERE year = :prev_year
                      AND ticker IS NOT NULL
                      AND ticker != ''
                    GROUP BY ticker
                )
                SELECT
                    cn.company_name,
                    c.ticker,
                    cs.sector,
                    c.total_spend,
                    c.market_cap,
                    CASE
                        WHEN c.market_cap > 0
                        THEN c.total_spend / c.market_cap
                        ELSE NULL
                    END AS avg_spend_mcap,
                    c.quarters_count,
                    p.total_spend AS prev_spend,
                    CASE
                        WHEN p.total_spend > 0
                        THEN ROUND(
                            (c.total_spend - p.total_spend) * 100.0 / p.total_spend,
                            1
                        )
                        ELSE NULL
                    END AS yoy_pct
                FROM curr_by_ticker c
                LEFT JOIN prev_by_ticker p ON c.ticker = p.ticker
                LEFT JOIN curr_name cn ON c.ticker = cn.ticker
                LEFT JOIN curr_sector cs ON c.ticker = cs.ticker
                ORDER BY c.total_spend DESC
                """,
                conn,
                params={
                    "curr_year": year,
                    "prev_year": prev_year,
                    "min_spend": min_spend,
                },
            )
        else:
            df = pd.read_sql_query(
                """
                SELECT
                    curr.company_name,
                    curr.ticker,
                    curr.sector,
                    curr.total_spend,
                    curr.market_cap,
                    curr.avg_spend_mcap,
                    curr.quarters_count,
                    prev.total_spend AS prev_spend,
                    CASE
                        WHEN prev.total_spend > 0
                        THEN ROUND(
                            (curr.total_spend - prev.total_spend) * 100.0 / prev.total_spend,
                            1
                        )
                        ELSE NULL
                    END AS yoy_pct
                FROM (
                    SELECT
                        company_name, ticker, sector,
                        SUM(total_lobbying_spend)    AS total_spend,
                        MAX(market_cap)              AS market_cap,
                        AVG(spend_to_mcap_ratio)     AS avg_spend_mcap,
                        COUNT(DISTINCT quarter)      AS quarters_count
                    FROM company_lobbying
                    WHERE year = ?
                    GROUP BY company_name, ticker, sector
                    HAVING total_spend >= ?
                ) curr
                LEFT JOIN (
                    SELECT company_name, SUM(total_lobbying_spend) AS total_spend
                    FROM company_lobbying
                    WHERE year = ?
                    GROUP BY company_name
                ) prev ON curr.company_name = prev.company_name
                ORDER BY curr.total_spend DESC
                """,
                conn,
                params=(year, min_spend, prev_year),
            )

        # ── OpenSecrets PAC/contribution data (optional, graceful if absent) ─
        os_spend: pd.Series = pd.Series(dtype=float)
        try:
            os_tbl = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name='opensecrets_contribs'",
                conn,
            )
            if not os_tbl.empty:
                os_df = pd.read_sql_query(
                    """
                    SELECT ticker,
                           COALESCE(total_contribs, 0) + COALESCE(pacs, 0) AS os_combined
                    FROM opensecrets_contribs
                    WHERE year = :year
                      AND ticker IS NOT NULL AND TRIM(ticker) != ''
                    """,
                    conn,
                    params={"year": year},
                )
                if not os_df.empty:
                    # Aggregate duplicates: multiple company_name rows can share a
                    # ticker (e.g. subsidiary aliases). Sum is correct because PAC
                    # contributions from all aliases roll up to the same public entity.
                    # Using set_index on a non-unique index raises InvalidIndexError.
                    os_spend = os_df.groupby("ticker", sort=False)["os_combined"].sum()
        except Exception:
            pass  # OS table absent or query failed – component scores 0 for all

    finally:
        conn.close()

    if df.empty:
        return df

    # ── Entity-level dedup ────────────────────────────────────────────────────
    df["entity_key"] = df["ticker"].where(
        df["ticker"].notna() & (df["ticker"].astype(str).str.strip() != ""),
        df["company_name"],
    )
    df = (
        df.sort_values("total_spend", ascending=False)
          .drop_duplicates("entity_key", keep="first")
          .drop(columns=["entity_key"])
          .reset_index(drop=True)
    )
    # ─────────────────────────────────────────────────────────────────────────

    # ── YoY component (0-40 pts) — concave sqrt curve ────────────────────────
    # Old linear formula capped at 300% → too easy to max out (e.g. $300K→$1.2M).
    # New sqrt curve caps at 500%; 100% growth ≈ 22 pts, 300% ≈ 33 pts, 500% = 40 pts.
    def _yoy_pts(pct):
        if pd.isna(pct):
            return 15.0          # neutral for new entrants
        pct = float(pct)
        if pct <= -100.0:
            return 0.0
        if pct <= 0.0:
            # Decline band: 0–8 pts for -100% → 0%
            return round((pct + 100.0) / 100.0 * 8.0, 1)
        # Growth band: concave sqrt curve, 8 pts at 0%, 40 pts at ≥500%
        capped = min(pct, 500.0)
        return round(8.0 + ((capped / 500.0) ** 0.5) * 32.0, 1)

    # ── Spend/MCap component (0-25 pts) — percentile rank ────────────────────
    # Companies WITHOUT market-cap data score 0.
    # Rescaled 30→25 to make room for the OpenSecrets PAC component.
    def _mcap_pts(series):
        """Rank each company's spend/mcap ratio within the scored universe."""
        notna = series.notna()
        result = pd.Series(0.0, index=series.index)   # 0 for no market-cap data
        if notna.sum() > 0:
            ranks = series[notna].rank(pct=True)
            result[notna] = (ranks * 25.0).round(1)
        return result

    # ── Consistency component (0-25 pts) ─────────────────────────────────────
    # Rescaled 30→25 to make room for the OpenSecrets PAC component.
    def _consistency_pts(q):
        return round((min(int(q), 4) / 4.0) * 25.0, 1)

    # ── OpenSecrets PAC component (0-10 pts) ─────────────────────────────────
    # Companies combining heavy lobbying with PAC contributions score higher.
    # Academic evidence: dual-channel (lobby + donate) firms have stronger policy
    # outcomes (Hutchens, Rego, Sheneman 2016).  Scores 0 when OS key not set.
    def _os_pts(series: pd.Series) -> pd.Series:
        """Percentile rank of (total_contribs + pacs) combined spend. 0-10 pts."""
        positive = series.notna() & (series > 0)
        result = pd.Series(0.0, index=series.index)
        if positive.sum() > 0:
            ranks = series[positive].rank(pct=True)
            result[positive] = (ranks * 10.0).round(1)
        return result

    df["os_combined"]    = df["ticker"].map(os_spend).fillna(0.0)
    df["yoy_pts"]         = df["yoy_pct"].apply(_yoy_pts)
    df["mcap_pts"]        = _mcap_pts(df["avg_spend_mcap"])
    df["consistency_pts"] = df["quarters_count"].apply(_consistency_pts)
    df["os_pts"]          = _os_pts(df["os_combined"])
    df["conviction_score"] = (
        df["yoy_pts"] + df["mcap_pts"] + df["consistency_pts"] + df["os_pts"]
    ).round(1)
    # Max: 40 + 25 + 25 + 10 = 100 (with OS data) | 40 + 25 + 25 = 90 (without)

    return df.sort_values("conviction_score", ascending=False).reset_index(drop=True)
