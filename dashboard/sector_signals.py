"""
Sector rotation, sector spend by year, and new-entrant signal backtests.
"""

import sqlite3

import pandas as pd
import streamlit as st

import config

from dashboard.benchmark import (
    _cost_pct_from_turnover,
    _equal_weight_turnover_pct,
    _q4_signal_window,
    _sanitize_ticker_list,
)
from dashboard import benchmark


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL 1 — SECTOR ROTATION
# ─────────────────────────────────────────────────────────────────────────────

# Map Yahoo Finance sector labels → SPDR sector ETF tickers
_SECTOR_ETF_MAP: dict[str, str] = {
    "Technology": "XLK",
    "Information Technology": "XLK",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Communication Services": "XLC",
    "Telecom": "XLC",
    "Telecommunications": "XLC",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Materials": "XLB",
}


@st.cache_data
def get_sector_rotation_signal(db_path, refresh_token=0):
    """
    For each year that has prior-year data, compute the sector-level lobbying
    spend YoY%, identify the top-ramping sector, map it to its SPDR ETF, then
    fetch the ETF's calendar-year return for the *following* year (the holding
    period) via yfinance.

    Signal logic: if sector X ramps lobbying hardest in year Y, buy that
    sector's ETF at the close of year Y and hold through year Y+1.

    Returns a DataFrame with columns:
        year, top_sector, etf_ticker, sector_yoy_pct, n_sectors,
        etf_return, spx_return, alpha
    (year = the signal year; returns are for year+1 holding period)
    """
    import sqlite3
    import yfinance as yf

    conn = sqlite3.connect(db_path)
    try:
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
        years = years_df["year"].tolist()
    finally:
        conn.close()

    if len(years) < 2:
        return pd.DataFrame()

    rows = []
    conn = sqlite3.connect(db_path)
    try:
        for year in years:
            prev_year = year - 1
            curr_s = pd.read_sql_query(
                """
                SELECT sector, SUM(total_lobbying_spend) AS spend
                FROM company_lobbying
                WHERE year = ? AND sector IS NOT NULL AND sector != ''
                GROUP BY sector HAVING spend > 0
                """,
                conn, params=(year,),
            )
            prev_s = pd.read_sql_query(
                """
                SELECT sector, SUM(total_lobbying_spend) AS spend
                FROM company_lobbying
                WHERE year = ? AND sector IS NOT NULL AND sector != ''
                GROUP BY sector HAVING spend > 0
                """,
                conn, params=(prev_year,),
            )
            if curr_s.empty or prev_s.empty:
                continue
            merged = curr_s.merge(prev_s, on="sector", suffixes=("_c", "_p"))
            if merged.empty:
                continue
            merged["yoy_pct"] = (
                (merged["spend_c"] - merged["spend_p"]) / merged["spend_p"] * 100
            ).round(1)
            merged["etf"] = merged["sector"].map(_SECTOR_ETF_MAP)
            mapped = merged[merged["etf"].notna()].copy()
            if mapped.empty:
                continue
            top = mapped.nlargest(1, "yoy_pct").iloc[0]
            rows.append({
                "year": int(year),
                "top_sector": top["sector"],
                "etf_ticker": top["etf"],
                "sector_yoy_pct": round(float(top["yoy_pct"]), 1),
                "n_sectors": int(len(mapped)),
                "all_sectors": mapped[["sector", "etf", "yoy_pct", "spend_c"]].to_dict("records"),
            })
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame()

    signal_df = pd.DataFrame(rows)

    # Fetch ETF + SPX year-end prices via yfinance
    etfs_needed = list(signal_df["etf_ticker"].unique())
    all_fetch = etfs_needed + ["^GSPC"]
    min_year = int(signal_df["year"].min())
    max_year = int(signal_df["year"].max())

    try:
        hist = yf.download(
            all_fetch,
            start=f"{min_year}-01-01",
            end=f"{max_year + 2}-01-31",
            auto_adjust=True,
            progress=False,
        )["Close"]
        if isinstance(hist, pd.Series):
            hist = hist.to_frame(name=all_fetch[0])
        hist.index = pd.to_datetime(hist.index)

        def _yr_end(ticker, yr):
            col = hist.get(ticker)
            if col is None:
                return None
            sub = col[col.index.year == yr].dropna()
            return float(sub.iloc[-1]) if not sub.empty else None

        etf_rets, spx_rets = [], []
        for _, row in signal_df.iterrows():
            sig_yr = int(row["year"])
            hold_yr = sig_yr + 1
            etf = row["etf_ticker"]
            p0_e = _yr_end(etf, sig_yr)
            p1_e = _yr_end(etf, hold_yr)
            p0_s = _yr_end("^GSPC", sig_yr)
            p1_s = _yr_end("^GSPC", hold_yr)
            etf_rets.append(
                round((p1_e / p0_e - 1) * 100, 2) if (p0_e and p1_e and p0_e > 0) else None
            )
            spx_rets.append(
                round((p1_s / p0_s - 1) * 100, 2) if (p0_s and p1_s and p0_s > 0) else None
            )

        signal_df["etf_return"] = etf_rets
        signal_df["spx_return"] = spx_rets
        signal_df["alpha"] = signal_df.apply(
            lambda r: round(r["etf_return"] - r["spx_return"], 2)
            if pd.notna(r.get("etf_return")) and pd.notna(r.get("spx_return"))
            else None,
            axis=1,
        )
    except Exception as exc:
        signal_df["etf_return"] = None
        signal_df["spx_return"] = None
        signal_df["alpha"] = None
        signal_df["_fetch_error"] = str(exc)

    out_cols = ["year", "top_sector", "etf_ticker", "sector_yoy_pct",
                "n_sectors", "etf_return", "spx_return", "alpha"]
    return signal_df[[c for c in out_cols if c in signal_df.columns]]


@st.cache_data
def get_sector_spend_by_year(db_path, refresh_token=0):
    """
    Returns a long-format DataFrame of sector lobbying spend per year,
    used to draw the sector ramp chart in the Signals tab.
    Columns: year, sector, etf, spend, yoy_pct
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT year, sector, SUM(total_lobbying_spend) AS spend
            FROM company_lobbying
            WHERE sector IS NOT NULL AND sector != ''
            GROUP BY year, sector
            ORDER BY year, sector
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame()

    df["etf"] = df["sector"].map(_SECTOR_ETF_MAP)
    df = df[df["etf"].notna()].copy()

    # Compute YoY% per sector
    df = df.sort_values(["sector", "year"])
    df["prev_spend"] = df.groupby("sector")["spend"].shift(1)
    df["yoy_pct"] = ((df["spend"] - df["prev_spend"]) / df["prev_spend"] * 100).round(1)
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL 2 — NEW ENTRANT / SPEND ACCELERATION
# ─────────────────────────────────────────────────────────────────────────────


@st.cache_data
def get_new_entrant_signal(
    db_path,
    min_curr_spend_m: float = 1.0,
    max_prev_spend_k: float = 200.0,
    refresh_token: int = 0,
):
    """
    Identify companies that crossed from minimal lobbying spend to significant
    spend in a single year (regulatory debut).

    Threshold defaults:
        - Current year spend >= min_curr_spend_m * $1M
        - Prior year spend  <  max_prev_spend_k * $1K  (or no prior-year record)

    Returns a tuple (detail_df, backtest_df):
        detail_df   – one row per company per signal year, includes return_1y
        backtest_df – aggregate by hold year: n_entrants, avg_return, spx_return, alpha
    """
    import sqlite3

    cost_bps = float(getattr(config, "BACKTEST_REBALANCE_COST_BPS", 0.0) or 0.0)

    conn = sqlite3.connect(db_path)
    try:
        chk = pd.read_sql_query("SELECT COUNT(*) AS n FROM stock_performance", conn)
        if chk["n"].iloc[0] == 0:
            return pd.DataFrame(), pd.DataFrame()

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

        min_curr = min_curr_spend_m * 1_000_000
        max_prev = max_prev_spend_k * 1_000

        detail_rows = []
        for year in years:
            prev_year = year - 1
            ne_df = pd.read_sql_query(
                """
                WITH curr_by_ticker AS (
                    SELECT
                        ticker,
                        SUM(total_lobbying_spend) AS curr_spend
                    FROM company_lobbying
                    WHERE year = ?
                      AND ticker IS NOT NULL
                      AND ticker != ''
                    GROUP BY ticker
                    HAVING curr_spend >= ?
                ),
                prev_by_ticker AS (
                    SELECT
                        ticker,
                        SUM(total_lobbying_spend) AS prev_spend
                    FROM company_lobbying
                    WHERE year = ?
                      AND ticker IS NOT NULL
                      AND ticker != ''
                    GROUP BY ticker
                ),
                curr_rep AS (
                    SELECT ticker, company_name, sector
                    FROM (
                        SELECT
                            ticker,
                            company_name,
                            sector,
                            SUM(total_lobbying_spend) AS spend,
                            ROW_NUMBER() OVER (
                                PARTITION BY ticker
                                ORDER BY SUM(total_lobbying_spend) DESC, company_name ASC
                            ) AS rn
                        FROM company_lobbying
                        WHERE year = ?
                          AND ticker IS NOT NULL
                          AND ticker != ''
                        GROUP BY ticker, company_name, sector
                    )
                    WHERE rn = 1
                )
                SELECT
                    rep.company_name,
                    curr.ticker,
                    rep.sector,
                    curr.curr_spend,
                    COALESCE(prev.prev_spend, 0) AS prev_spend
                FROM curr_by_ticker curr
                LEFT JOIN prev_by_ticker prev ON curr.ticker = prev.ticker
                LEFT JOIN curr_rep rep ON curr.ticker = rep.ticker
                WHERE COALESCE(prev.prev_spend, 0) < ?
                ORDER BY curr.curr_spend DESC
                """,
                conn,
                params=(year, min_curr, prev_year, year, max_prev),
            )
            if ne_df.empty:
                continue

            # Batch-fetch returns from stock_performance
            tickers = sorted(set(ne_df["ticker"].dropna().astype(str).str.strip().tolist()))
            if not tickers:
                continue
            ref_date, date_lo, date_hi = _q4_signal_window(int(year), window_days=46)
            ticker_placeholders = ",".join("?" for _ in tickers)

            ret_df = pd.read_sql_query(
                f"""
                WITH ranked AS (
                    SELECT ticker, return_1y,
                           ROW_NUMBER() OVER (
                               PARTITION BY ticker
                               ORDER BY ABS(JULIANDAY(date) - JULIANDAY(?))
                           ) AS rn
                    FROM stock_performance
                    WHERE date BETWEEN ? AND ?
                      AND ticker IN ({ticker_placeholders})
                )
                SELECT ticker, return_1y FROM ranked WHERE rn = 1
                """,
                conn,
                params=[ref_date, date_lo, date_hi, *tickers],
            )
            ret_map = dict(zip(ret_df["ticker"], ret_df["return_1y"]))

            for _, row in ne_df.iterrows():
                signal_year = int(year)
                detail_rows.append({
                    "signal_year": signal_year,
                    "hold_year": signal_year + 1,
                    "company_name": row["company_name"],
                    "ticker": row["ticker"],
                    "sector": row["sector"],
                    "curr_spend": float(row["curr_spend"]),
                    "prev_spend": float(row["prev_spend"]),
                    "return_1y": ret_map.get(row["ticker"]),
                })
    finally:
        conn.close()

    if not detail_rows:
        return pd.DataFrame(), pd.DataFrame()

    detail_df = pd.DataFrame(detail_rows)

    # Backtest aggregation by year
    bt_rows = []
    for hold_year in sorted(detail_df["hold_year"].unique()):
        yr = detail_df[detail_df["hold_year"] == hold_year]
        valid = yr["return_1y"].dropna()
        if len(valid) < 3:
            continue
        hold_year_int = int(hold_year)
        gross_ret = round(float(valid.mean()), 2)
        constituents = _sanitize_ticker_list(yr["ticker"].dropna().tolist())
        bt_rows.append({
            "year": hold_year_int,
            "signal_year": hold_year_int - 1,
            "n_entrants": int(len(yr)),
            "n_with_returns": int(len(valid)),
            "avg_return_gross": gross_ret,
            "constituents": constituents,
        })

    if not bt_rows:
        return detail_df, pd.DataFrame()

    bt_df = pd.DataFrame(bt_rows).sort_values("signal_year").reset_index(drop=True)
    prev_constituents = []
    turnover_vals = []
    for constituents in bt_df["constituents"].tolist():
        turnover_vals.append(
            _equal_weight_turnover_pct(prev_constituents, constituents)
        )
        prev_constituents = constituents
    bt_df["turnover_pct"] = turnover_vals
    bt_df["cost_applied_pct"] = bt_df["turnover_pct"].apply(
        lambda t: _cost_pct_from_turnover(t, cost_bps)
    )
    bt_df["avg_return"] = (
        bt_df["avg_return_gross"] - bt_df["cost_applied_pct"]
    ).round(2)

    spx_dict, _ = benchmark._fetch_spx_returns_for_signal_years(
        bt_df["signal_year"].astype(int).tolist(),
        prefer_forward_window=True,
    )
    bt_df["spx_return"] = bt_df["year"].map(spx_dict)
    bt_df["alpha"] = bt_df.apply(
        lambda r: round(r["avg_return"] - r["spx_return"], 2)
        if pd.notna(r.get("spx_return")) and pd.notna(r.get("avg_return"))
        else None,
        axis=1,
    )
    bt_df = bt_df.drop(columns=["constituents"], errors="ignore")
    return detail_df, bt_df
