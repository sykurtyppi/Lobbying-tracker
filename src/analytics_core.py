"""
Core analytics/backtest functions decoupled from Streamlit UI runtime.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta
from typing import Iterable

import numpy as np
import pandas as pd

import config


def _sanitize_ticker_list(tickers: Iterable[str] | None) -> list[str]:
    """Normalize ticker iterable into sorted unique uppercase symbols."""
    cleaned = {
        str(t).strip().upper()
        for t in (tickers or [])
        if str(t).strip() and str(t).strip().upper() != "NAN"
    }
    return sorted(cleaned)


def _equal_weight_turnover_pct(prev_tickers, curr_tickers) -> float:
    """
    Equal-weight one-period turnover as a percent of portfolio notional.
    100.0 means full portfolio turnover.
    """
    prev = _sanitize_ticker_list(prev_tickers)
    curr = _sanitize_ticker_list(curr_tickers)
    if not prev and not curr:
        return 0.0
    if not prev or not curr:
        return 100.0

    union = set(prev) | set(curr)
    w_prev = 1.0 / len(prev)
    w_curr = 1.0 / len(curr)
    gross_change = 0.0
    for t in union:
        prev_w = w_prev if t in prev else 0.0
        curr_w = w_curr if t in curr else 0.0
        gross_change += abs(curr_w - prev_w)
    return round(0.5 * gross_change * 100.0, 2)


def _cost_pct_from_turnover(turnover_pct, cost_bps: float) -> float:
    """Convert turnover% and bps-per-100%-turnover into return percentage points."""
    if turnover_pct is None or pd.isna(turnover_pct):
        return 0.0
    return round((float(turnover_pct) / 100.0) * (float(cost_bps) / 100.0), 4)


def _q4_signal_window(year: int, window_days: int = 46) -> tuple[str, str, str]:
    """Build lag-adjusted Q4 signal window (ref_date, date_lo, date_hi)."""
    lag_days = max(int(getattr(config, "LDA_FILING_LAG_DAYS", 20) or 0), 0)
    signal_dt = pd.Timestamp(year=int(year), month=12, day=31) + pd.Timedelta(days=lag_days)
    date_lo = (signal_dt - pd.Timedelta(days=window_days)).strftime("%Y-%m-%d")
    date_hi = (signal_dt + pd.Timedelta(days=window_days)).strftime("%Y-%m-%d")
    return signal_dt.strftime("%Y-%m-%d"), date_lo, date_hi


def _fetch_spx_annual_returns(min_year: int, max_year: int) -> tuple[dict, str | None]:
    """
    Return (spx_dict, error_or_None) where spx_dict maps year -> calendar-year
    total return (%), using a fixed hardcoded benchmark table.
    """
    spx_known_total_return: dict[int, float] = {
        2019: 31.49,
        2020: 18.40,
        2021: 28.71,
        2022: -18.11,
        2023: 26.29,
        2024: 25.02,
        2025: 17.88,
    }

    spx_dict = {
        yr: spx_known_total_return[yr]
        for yr in range(min_year, max_year + 1)
        if yr in spx_known_total_return
    }
    return spx_dict, None


def _fetch_spx_returns_for_signal_years(
    signal_years,
    prefer_forward_window: bool = True,
    window_days: int = 46,
) -> tuple[dict, str | None]:
    """
    Return S&P benchmark returns mapped by hold year (signal_year + 1).
    """
    years = sorted(
        {
            int(y)
            for y in (signal_years or [])
            if y is not None and not pd.isna(y)
        }
    )
    if not years:
        return {}, None

    hold_years = [y + 1 for y in years]
    annual_map, _ = _fetch_spx_annual_returns(min(hold_years), max(hold_years))
    if not prefer_forward_window:
        return annual_map, None

    try:
        import yfinance as yf

        first_ref = pd.to_datetime(_q4_signal_window(min(years), window_days=window_days)[0])
        last_ref = pd.to_datetime(_q4_signal_window(max(years), window_days=window_days)[0])
        hist_start = (first_ref - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
        hist_end = (last_ref + pd.Timedelta(days=390)).strftime("%Y-%m-%d")

        hist = yf.Ticker("^GSPC").history(
            start=hist_start,
            end=hist_end,
            auto_adjust=True,
        )
        if hist.empty:
            raise ValueError("empty history for ^GSPC")

        hist = hist.copy()
        hist.index = pd.to_datetime(hist.index)
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)

        def _next_close(target_dt: pd.Timestamp):
            sub = hist.loc[hist.index >= target_dt]
            if sub.empty:
                return None
            return float(sub.iloc[0]["Close"])

        forward_map = {}
        for signal_year in years:
            ref_dt = pd.to_datetime(
                _q4_signal_window(int(signal_year), window_days=window_days)[0]
            )
            end_dt = ref_dt + pd.Timedelta(days=365)
            p0 = _next_close(ref_dt)
            p1 = _next_close(end_dt)
            if p0 is None or p1 is None or p0 <= 0:
                continue
            forward_map[int(signal_year) + 1] = round(((p1 - p0) / p0) * 100.0, 2)

        if not forward_map:
            raise ValueError("unable to derive any forward-window S&P returns")

        merged_map = {}
        fallback_years = []
        missing_years = []
        for hold_year in hold_years:
            if hold_year in forward_map:
                merged_map[int(hold_year)] = float(forward_map[hold_year])
            elif hold_year in annual_map:
                merged_map[int(hold_year)] = float(annual_map[hold_year])
                fallback_years.append(int(hold_year))
            else:
                missing_years.append(int(hold_year))

        msg_parts = []
        if fallback_years:
            msg_parts.append(
                "S&P benchmark mixed mode: forward-window returns used where available; "
                f"calendar fallback for hold year(s): {', '.join(str(y) for y in sorted(fallback_years))}."
            )
        if missing_years:
            msg_parts.append(
                "S&P benchmark missing for hold year(s): "
                + ", ".join(str(y) for y in sorted(missing_years))
                + "."
            )
        return merged_map, (" ".join(msg_parts) if msg_parts else None)
    except Exception as exc:
        if annual_map:
            return (
                annual_map,
                (
                    "S&P forward-window fetch unavailable; using hardcoded "
                    f"calendar-year returns. ({exc})"
                ),
            )
        return {}, f"S&P benchmark unavailable ({exc})"


def get_signal_returns(db_path, year):
    """
    Join company_lobbying Q4 with stock_performance to show forward returns
    for top lobbying companies in `year`.
    """
    conn = sqlite3.connect(db_path)
    try:
        chk = pd.read_sql_query(
            "SELECT COUNT(*) AS n FROM stock_performance", conn
        )
        if chk["n"].iloc[0] == 0:
            return pd.DataFrame()

        ref_date, date_lo, date_hi = _q4_signal_window(int(year), window_days=46)

        df = pd.read_sql_query(
            """
            WITH ranked_sp AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker
                           ORDER BY ABS(JULIANDAY(date) - JULIANDAY(:ref_date))
                       ) AS rn
                FROM stock_performance
                WHERE date BETWEEN :date_lo AND :date_hi
            ),
            nearest_sp AS (
                SELECT * FROM ranked_sp WHERE rn = 1
            ),
            cl_grouped AS (
                SELECT
                    company_name,
                    ticker,
                    sector,
                    SUM(total_lobbying_spend) AS total_spend
                FROM company_lobbying
                WHERE year = :year
                  AND quarter = 'Q4'
                  AND ticker IS NOT NULL
                  AND ticker != ''
                GROUP BY company_name, ticker, sector
                HAVING total_spend >= 1000000
            ),
            cl_ranked AS (
                SELECT
                    company_name,
                    ticker,
                    sector,
                    total_spend,
                    SUM(total_spend) OVER (PARTITION BY ticker) AS ticker_total_spend,
                    ROW_NUMBER() OVER (
                        PARTITION BY ticker
                        ORDER BY total_spend DESC, company_name ASC
                    ) AS rn
                FROM cl_grouped
            ),
            cl_dedup AS (
                SELECT
                    company_name,
                    ticker,
                    sector,
                    ticker_total_spend AS total_spend
                FROM cl_ranked
                WHERE rn = 1
            )
            SELECT
                cl.company_name,
                cl.ticker,
                cl.sector,
                cl.total_spend,
                sp.date    AS signal_date,
                sp.return_1m,
                sp.return_3m,
                sp.return_6m,
                sp.return_1y
            FROM cl_dedup cl
            JOIN nearest_sp sp ON cl.ticker = sp.ticker
            ORDER BY cl.total_spend DESC
            """,
            conn,
            params={"year": year, "ref_date": ref_date, "date_lo": date_lo, "date_hi": date_hi},
        )
    finally:
        conn.close()

    return df


def get_benchmark_comparison(db_path):
    """Year-by-year strategy return vs S&P 500 benchmark."""
    cost_bps = float(getattr(config, "BACKTEST_REBALANCE_COST_BPS", 0.0) or 0.0)
    min_return_cov = float(
        getattr(config, "MIN_BACKTEST_RETURN_COVERAGE_PCT", 80.0) or 0.0
    )

    conn = sqlite3.connect(db_path)
    try:
        chk = pd.read_sql_query("SELECT COUNT(*) AS n FROM stock_performance", conn)
        if chk["n"].iloc[0] == 0:
            return pd.DataFrame()

        years_df = pd.read_sql_query(
            """
            SELECT year
            FROM (
                SELECT year, COUNT(DISTINCT quarter) AS n_quarters
                FROM company_lobbying
                GROUP BY year
            )
            WHERE n_quarters = 4
            ORDER BY year
            """,
            conn,
        )

        rows = []
        for year in years_df["year"].tolist():
            ref_date, date_lo, date_hi = _q4_signal_window(int(year), window_days=46)

            df_yr = pd.read_sql_query(
                """
                WITH ranked_sp AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY ticker
                               ORDER BY ABS(JULIANDAY(date) - JULIANDAY(:ref_date))
                           ) AS rn
                    FROM stock_performance
                    WHERE date BETWEEN :date_lo AND :date_hi
                ),
                nearest_sp AS (SELECT * FROM ranked_sp WHERE rn = 1),
                cl_grouped AS (
                    SELECT ticker, SUM(total_lobbying_spend) AS total_spend
                    FROM company_lobbying
                    WHERE year = :year AND quarter = 'Q4'
                      AND ticker IS NOT NULL AND ticker != ''
                    GROUP BY ticker
                    HAVING total_spend >= 1000000
                )
                SELECT
                    cl.ticker,
                    cl.total_spend,
                    sp.return_1y
                FROM cl_grouped cl
                JOIN nearest_sp sp ON cl.ticker = sp.ticker
                """,
                conn,
                params={"year": year, "ref_date": ref_date, "date_lo": date_lo, "date_hi": date_hi},
            )
            if df_yr.empty:
                continue

            valid_df = df_yr[df_yr["return_1y"].notna()].copy()
            mapped_n = int(len(df_yr))
            valid_n = int(len(valid_df))
            return_cov = (valid_n / mapped_n * 100.0) if mapped_n > 0 else 0.0
            if valid_n < 3 or return_cov < min_return_cov:
                continue

            stats_row = pd.read_sql_query(
                """
                WITH q4_entities AS (
                    SELECT
                        CASE
                            WHEN ticker IS NOT NULL AND TRIM(ticker) != ''
                            THEN UPPER(TRIM(ticker))
                            ELSE UPPER(TRIM(company_name))
                        END AS entity_key,
                        SUM(total_lobbying_spend) AS entity_spend,
                        MAX(CASE
                            WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN 1
                            ELSE 0
                        END) AS has_ticker
                    FROM company_lobbying
                    WHERE year = :year
                      AND quarter = 'Q4'
                    GROUP BY entity_key
                )
                SELECT
                    COUNT(*) AS universe_entities,
                    SUM(entity_spend) AS universe_spend
                FROM q4_entities
                WHERE entity_spend >= 1000000
                """,
                conn,
                params={"year": year},
            ).iloc[0]

            signal_year = int(year)
            gross_ret = round(float(valid_df["return_1y"].mean()), 2)
            constituents = _sanitize_ticker_list(valid_df["ticker"].tolist())
            universe_entities = int(stats_row.get("universe_entities") or 0)
            universe_spend = float(stats_row.get("universe_spend") or 0.0)
            included_spend = float(valid_df["total_spend"].sum() or 0.0)

            entity_cov = (
                (len(constituents) / universe_entities) * 100.0
                if universe_entities > 0
                else 0.0
            )
            spend_cov = (
                (included_spend / universe_spend) * 100.0
                if universe_spend > 0
                else 0.0
            )

            rows.append(
                {
                    "signal_year": signal_year,
                    "year": signal_year + 1,
                    "strategy_return_gross": gross_ret,
                    "n_companies": valid_n,
                    "mapped_companies": mapped_n,
                    "return_coverage_pct": round(return_cov, 1),
                    "entity_coverage_pct": round(entity_cov, 1),
                    "spend_coverage_pct": round(spend_cov, 1),
                    "constituents": constituents,
                }
            )
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame()

    strat_df = pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
    prev_constituents = []
    turnover_vals = []
    for constituents in strat_df["constituents"].tolist():
        turnover_vals.append(
            _equal_weight_turnover_pct(prev_constituents, constituents)
        )
        prev_constituents = constituents

    strat_df["turnover_pct"] = turnover_vals
    strat_df["cost_applied_pct"] = strat_df["turnover_pct"].apply(
        lambda t: _cost_pct_from_turnover(t, cost_bps)
    )
    strat_df["strategy_return"] = (
        strat_df["strategy_return_gross"] - strat_df["cost_applied_pct"]
    ).round(2)

    spx_dict, spx_error = _fetch_spx_returns_for_signal_years(
        strat_df["signal_year"].astype(int).tolist(),
        prefer_forward_window=True,
    )
    strat_df["spx_return"] = strat_df["year"].map(spx_dict)
    if spx_error and strat_df["spx_return"].notna().all():
        spx_error = None
    strat_df["_spx_error"] = spx_error
    strat_df = strat_df.drop(columns=["constituents"], errors="ignore")
    return strat_df


def _get_complete_signal_years(db_path: str) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        years = pd.read_sql_query(
            """
            SELECT year
            FROM (
                SELECT year, COUNT(DISTINCT quarter) AS n_quarters
                FROM company_lobbying
                GROUP BY year
            )
            WHERE n_quarters = 4
            ORDER BY year
            """,
            conn,
        )["year"].tolist()
    finally:
        conn.close()
    return [int(y) for y in years]


def get_acceleration_features(
    db_path,
    year: int,
    min_spend_m: float = 1.0,
    ticker_only: bool = True,
):
    """Build per-entity acceleration features for a given signal year."""
    min_hist_year = int(year) - int(getattr(config, "ACCELERATION_LOOKBACK_YEARS", 5))
    min_spend = float(min_spend_m) * 1_000_000.0
    min_feature_count = int(getattr(config, "ACCELERATION_MIN_FEATURE_COUNT", 3))

    conn = sqlite3.connect(db_path)
    try:
        annual_df = pd.read_sql_query(
            """
            WITH base AS (
                SELECT
                    CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE UPPER(TRIM(company_name))
                    END AS entity_key,
                    CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE NULL
                    END AS ticker_norm,
                    TRIM(company_name) AS company_name,
                    TRIM(COALESCE(sector, '')) AS sector,
                    year,
                    total_lobbying_spend,
                    market_cap
                FROM company_lobbying
                WHERE year BETWEEN :min_hist_year AND :year
                  AND total_lobbying_spend > 0
            ),
            annual AS (
                SELECT
                    entity_key,
                    year,
                    SUM(total_lobbying_spend) AS annual_spend,
                    MAX(market_cap) AS market_cap
                FROM base
                GROUP BY entity_key, year
            ),
            rep AS (
                SELECT entity_key, year, company_name, sector, ticker_norm
                FROM (
                    SELECT
                        entity_key,
                        year,
                        company_name,
                        sector,
                        ticker_norm,
                        SUM(total_lobbying_spend) AS spend,
                        ROW_NUMBER() OVER (
                            PARTITION BY entity_key, year
                            ORDER BY SUM(total_lobbying_spend) DESC, company_name ASC
                        ) AS rn
                    FROM base
                    GROUP BY entity_key, year, company_name, sector, ticker_norm
                )
                WHERE rn = 1
            )
            SELECT
                a.entity_key,
                a.year,
                a.annual_spend,
                a.market_cap,
                r.company_name,
                NULLIF(r.ticker_norm, '') AS ticker,
                NULLIF(r.sector, '') AS sector
            FROM annual a
            LEFT JOIN rep r
              ON a.entity_key = r.entity_key
             AND a.year = r.year
            ORDER BY a.entity_key, a.year
            """,
            conn,
            params={"min_hist_year": min_hist_year, "year": int(year)},
        )

        quarterly_df = pd.read_sql_query(
            """
            SELECT
                CASE
                    WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                    ELSE UPPER(TRIM(company_name))
                END AS entity_key,
                year,
                quarter,
                SUM(total_lobbying_spend) AS quarter_spend
            FROM company_lobbying
            WHERE year BETWEEN :min_hist_year AND :year
              AND total_lobbying_spend > 0
              AND quarter IN ('Q1', 'Q2', 'Q3', 'Q4')
            GROUP BY entity_key, year, quarter
            ORDER BY entity_key, year, quarter
            """,
            conn,
            params={"min_hist_year": min_hist_year, "year": int(year)},
        )
    finally:
        conn.close()

    if annual_df.empty:
        return pd.DataFrame()

    annual_df["year"] = pd.to_numeric(annual_df["year"], errors="coerce")
    annual_df["annual_spend"] = pd.to_numeric(annual_df["annual_spend"], errors="coerce")
    annual_df["market_cap"] = pd.to_numeric(annual_df["market_cap"], errors="coerce")
    annual_df = annual_df.dropna(subset=["year", "annual_spend"]).copy()
    annual_df["year"] = annual_df["year"].astype(int)
    annual_df = annual_df[annual_df["year"] <= int(year)].copy()

    curr_df = annual_df[annual_df["year"] == int(year)].copy()
    if curr_df.empty:
        return pd.DataFrame()

    curr_df = curr_df[curr_df["annual_spend"] >= min_spend].copy()
    if ticker_only:
        curr_df = curr_df[curr_df["ticker"].notna() & (curr_df["ticker"].str.strip() != "")].copy()
    if curr_df.empty:
        return pd.DataFrame()

    rows = []
    annual_by_entity = {
        ent: grp.sort_values("year").reset_index(drop=True)
        for ent, grp in annual_df.groupby("entity_key", dropna=False)
    }

    quarterly_df["year"] = pd.to_numeric(quarterly_df["year"], errors="coerce")
    quarterly_df["quarter_spend"] = pd.to_numeric(quarterly_df["quarter_spend"], errors="coerce")
    quarterly_df = quarterly_df.dropna(subset=["year", "quarter_spend"]).copy()
    quarterly_df["year"] = quarterly_df["year"].astype(int)
    quarterly_df["q_num"] = quarterly_df["quarter"].map({"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4})
    quarterly_df["period_idx"] = quarterly_df["year"] * 4 + quarterly_df["q_num"]
    quarterly_by_entity = {
        ent: grp.sort_values("period_idx").reset_index(drop=True)
        for ent, grp in quarterly_df.groupby("entity_key", dropna=False)
    }

    for _, row in curr_df.iterrows():
        entity_key = row["entity_key"]
        hist = annual_by_entity.get(entity_key)
        if hist is None or hist.empty:
            continue

        curr_spend = float(row["annual_spend"])
        prev_hist = hist[hist["year"] < int(year)]
        prev_row = prev_hist.iloc[-1] if not prev_hist.empty else None
        prev_spend = float(prev_row["annual_spend"]) if prev_row is not None else None

        if prev_spend is not None and prev_spend > 0:
            yoy_pct = round((curr_spend - prev_spend) / prev_spend * 100.0, 1)
        else:
            yoy_pct = None

        hist_vals = prev_hist["annual_spend"].dropna().astype(float)
        if len(hist_vals) >= 2 and float(hist_vals.std(ddof=0)) > 0:
            hist_spend_z = (curr_spend - float(hist_vals.mean())) / float(hist_vals.std(ddof=0))
        else:
            hist_spend_z = None

        q_hist = quarterly_by_entity.get(entity_key)
        qoq_pct = None
        if q_hist is not None and not q_hist.empty:
            q_curr = q_hist[
                (q_hist["year"] == int(year)) & (q_hist["quarter"] == "Q4")
            ]
            q_prev = q_hist[
                (q_hist["year"] == int(year)) & (q_hist["quarter"] == "Q3")
            ]
            if not q_curr.empty and not q_prev.empty:
                prev_q_spend = float(q_prev.iloc[-1]["quarter_spend"])
                curr_q_spend = float(q_curr.iloc[-1]["quarter_spend"])
                if prev_q_spend > 0:
                    qoq_pct = round((curr_q_spend - prev_q_spend) / prev_q_spend * 100.0, 1)

        mcap_ratio_yoy_pct = None
        if (
            prev_row is not None
            and pd.notna(prev_row["market_cap"])
            and float(prev_row["market_cap"]) > 0
            and pd.notna(row["market_cap"])
            and float(row["market_cap"]) > 0
            and prev_spend is not None
            and prev_spend > 0
        ):
            prev_ratio = prev_spend / float(prev_row["market_cap"])
            curr_ratio = curr_spend / float(row["market_cap"])
            if prev_ratio > 0:
                mcap_ratio_yoy_pct = round((curr_ratio - prev_ratio) / prev_ratio * 100.0, 1)

        rows.append(
            {
                "entity_key": entity_key,
                "ticker": row["ticker"],
                "company_name": row["company_name"],
                "sector": row["sector"],
                "signal_year": int(year),
                "annual_spend": curr_spend,
                "market_cap": float(row["market_cap"]) if pd.notna(row["market_cap"]) else None,
                "yoy_pct": yoy_pct,
                "qoq_pct": qoq_pct,
                "hist_spend_z": round(float(hist_spend_z), 3) if hist_spend_z is not None else None,
                "mcap_ratio_yoy_pct": mcap_ratio_yoy_pct,
            }
        )

    feat_df = pd.DataFrame(rows)
    if feat_df.empty:
        return feat_df

    sec_stats = (
        feat_df.groupby("sector", dropna=False)["yoy_pct"]
        .agg(["mean", "std"])
        .rename(columns={"mean": "sector_yoy_mean", "std": "sector_yoy_std"})
    )
    feat_df = feat_df.merge(sec_stats, left_on="sector", right_index=True, how="left")
    feat_df["sector_spike_z"] = feat_df.apply(
        lambda r: (
            (r["yoy_pct"] - r["sector_yoy_mean"]) / r["sector_yoy_std"]
            if pd.notna(r["yoy_pct"]) and pd.notna(r["sector_yoy_std"]) and float(r["sector_yoy_std"]) > 0
            else None
        ),
        axis=1,
    )

    score_inputs = {
        "yoy_rank": "yoy_pct",
        "qoq_rank": "qoq_pct",
        "hist_z_rank": "hist_spend_z",
        "sector_spike_rank": "sector_spike_z",
        "mcap_ratio_rank": "mcap_ratio_yoy_pct",
    }
    rank_cols = []
    for out_col, in_col in score_inputs.items():
        if in_col in feat_df.columns:
            ranked = feat_df[in_col].rank(pct=True)
            feat_df[out_col] = (ranked * 100.0).where(feat_df[in_col].notna(), other=None)
            rank_cols.append(out_col)

    feat_df["feature_count"] = feat_df[rank_cols].notna().sum(axis=1)
    feat_df["accel_score"] = feat_df[rank_cols].mean(axis=1, skipna=True)
    feat_df.loc[feat_df["feature_count"] < min_feature_count, "accel_score"] = None

    feat_df = feat_df.sort_values(
        ["accel_score", "annual_spend"], ascending=[False, False]
    ).reset_index(drop=True)
    return feat_df


def _build_acceleration_year_frame(
    db_path: str,
    signal_year: int,
    min_spend_m: float,
) -> pd.DataFrame:
    feat_df = get_acceleration_features(
        db_path,
        year=int(signal_year),
        min_spend_m=min_spend_m,
        ticker_only=True,
    )
    if feat_df.empty:
        return pd.DataFrame()

    ret_df = get_signal_returns(db_path, int(signal_year))
    if ret_df.empty:
        return pd.DataFrame()

    merged = feat_df.merge(
        ret_df[
            [
                "ticker",
                "return_1m",
                "return_3m",
                "return_6m",
                "return_1y",
                "total_spend",
                "signal_date",
            ]
        ],
        on="ticker",
        how="inner",
        suffixes=("_feat", "_ret"),
    )
    if merged.empty:
        return pd.DataFrame()
    merged["signal_year"] = int(signal_year)
    merged["year"] = int(signal_year) + 1
    return merged


def get_acceleration_oos_split_backtest(
    db_path,
    top_n: int = 20,
    min_spend_m: float = 1.0,
    train_start: int = 2019,
    train_end: int = 2022,
    test_start: int = 2023,
    test_end: int = 2026,
):
    """
    Out-of-sample split:
      - Fit linear factor weights on train years.
      - Apply frozen weights to all years.
      - Report train vs test performance separately.
    """
    years = _get_complete_signal_years(db_path)
    years = [y for y in years if y >= int(train_start) and y <= int(test_end)]
    if not years:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    frames = []
    for signal_year in years:
        yr = _build_acceleration_year_frame(
            db_path, signal_year, min_spend_m=min_spend_m
        )
        if not yr.empty:
            frames.append(yr)
    if not frames:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    full_df = pd.concat(frames, ignore_index=True)
    feature_cols = [
        "yoy_rank",
        "qoq_rank",
        "hist_z_rank",
        "sector_spike_rank",
        "mcap_ratio_rank",
    ]
    model_df = full_df[
        full_df["signal_year"].between(int(train_start), int(train_end))
        & full_df["return_1y"].notna()
    ].copy()
    if model_df.empty or len(model_df) < (len(feature_cols) + 10):
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    x_train = model_df[feature_cols].copy().fillna(50.0)
    y_train = model_df["return_1y"].astype(float).values
    mu = x_train.mean()
    sigma = x_train.std(ddof=0).replace(0, 1.0)
    xz = (x_train - mu) / sigma

    ridge = float(getattr(config, "OOS_RIDGE_L2", 1.0))
    x_mat = np.column_stack([np.ones(len(xz)), xz.values])
    eye = np.eye(x_mat.shape[1], dtype=float)
    eye[0, 0] = 0.0
    beta = np.linalg.pinv(x_mat.T @ x_mat + ridge * eye) @ (x_mat.T @ y_train)

    coef_rows = [{"feature": "intercept", "weight": float(beta[0])}]
    for i, col in enumerate(feature_cols, start=1):
        coef_rows.append({"feature": col, "weight": float(beta[i])})
    coef_df = pd.DataFrame(coef_rows)

    annual_rows = []
    for signal_year in sorted(full_df["signal_year"].dropna().astype(int).unique()):
        yr = full_df[full_df["signal_year"] == int(signal_year)].copy()
        if yr.empty:
            continue

        x_yr = yr[feature_cols].copy().fillna(50.0)
        xz_yr = (x_yr - mu) / sigma
        yr["model_score"] = float(beta[0]) + (xz_yr.values @ beta[1:])
        yr = yr.sort_values(["model_score", "annual_spend"], ascending=[False, False])

        top_df = yr.head(int(top_n)).copy()
        if top_df.empty:
            continue

        split = "Other"
        if int(train_start) <= int(signal_year) <= int(train_end):
            split = "Train"
        elif int(test_start) <= int(signal_year) <= int(test_end):
            split = "Test"

        row = {
            "signal_year": int(signal_year),
            "year": int(signal_year) + 1,
            "split": split,
            "n_universe": int(len(yr)),
            "n_top": int(len(top_df)),
        }
        for col in ["return_3m", "return_6m", "return_1y"]:
            top_ret = top_df[col].dropna()
            univ_ret = yr[col].dropna()
            row[f"top_{col}"] = round(float(top_ret.mean()), 2) if not top_ret.empty else None
            row[f"universe_{col}"] = round(float(univ_ret.mean()), 2) if not univ_ret.empty else None
            if row[f"top_{col}"] is not None and row[f"universe_{col}"] is not None:
                row[f"alpha_{col}"] = round(
                    row[f"top_{col}"] - row[f"universe_{col}"], 2
                )
            else:
                row[f"alpha_{col}"] = None
        annual_rows.append(row)

    annual_df = pd.DataFrame(annual_rows).sort_values("signal_year").reset_index(drop=True)
    if annual_df.empty:
        return pd.DataFrame(), pd.DataFrame(), coef_df

    summary_rows = []
    for split_label in ["Train", "Test"]:
        sub = annual_df[annual_df["split"] == split_label].copy()
        if sub.empty:
            continue
        summary_rows.append(
            {
                "Split": split_label,
                "Years": int(len(sub)),
                "Avg Alpha 3M": round(float(sub["alpha_return_3m"].dropna().mean()), 2)
                if sub["alpha_return_3m"].notna().any()
                else None,
                "Avg Alpha 6M": round(float(sub["alpha_return_6m"].dropna().mean()), 2)
                if sub["alpha_return_6m"].notna().any()
                else None,
                "Avg Alpha 1Y": round(float(sub["alpha_return_1y"].dropna().mean()), 2)
                if sub["alpha_return_1y"].notna().any()
                else None,
                "1Y Win Rate": round(float((sub["alpha_return_1y"] > 0).mean() * 100.0), 1)
                if sub["alpha_return_1y"].notna().any()
                else None,
            }
        )
    summary_df = pd.DataFrame(summary_rows)
    return annual_df, summary_df, coef_df
