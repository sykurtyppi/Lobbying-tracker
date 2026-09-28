"""
Company research queries: search, annual spend, stock snapshots, lobbyist firms, sector peers.
"""

import pandas as pd
import streamlit as st


@st.cache_data
def search_companies(db_path, query: str, refresh_token=0) -> pd.DataFrame:
    """
    Search company_lobbying for tickers or company names matching `query`.
    Returns one row per unique ticker (or company_name if no ticker),
    sorted by total lobbying spend descending.
    """
    import sqlite3

    q = query.strip().upper()
    if len(q) < 2:
        return pd.DataFrame()

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                ticker,
                sector,
                SUM(total_lobbying_spend)  AS total_spend,
                COUNT(DISTINCT year)       AS years_active,
                MAX(year)                  AS latest_year,
                MIN(year)                  AS earliest_year
            FROM company_lobbying
            WHERE ticker IS NOT NULL AND ticker != ''
              AND (UPPER(ticker) = ?
                   OR UPPER(company_name) LIKE ?)
            GROUP BY ticker
            ORDER BY total_spend DESC
            LIMIT 30
            """,
            conn,
            params=(q, f"%{q}%"),
        )
        # For each ticker, fetch the highest-spend entity name as display name
        if not df.empty:
            tickers = df["ticker"].tolist()
            ph = ",".join("?" for _ in tickers)
            names_df = pd.read_sql_query(
                f"""
                SELECT ticker,
                       company_name AS display_name
                FROM (
                    SELECT ticker, company_name,
                           SUM(total_lobbying_spend) AS s,
                           ROW_NUMBER() OVER (
                               PARTITION BY ticker ORDER BY SUM(total_lobbying_spend) DESC
                           ) AS rn
                    FROM company_lobbying
                    WHERE ticker IN ({ph})
                    GROUP BY ticker, company_name
                ) WHERE rn = 1
                """,
                conn,
                params=tickers,
            )
            df = df.merge(names_df, on="ticker", how="left")
    finally:
        conn.close()

    return df


@st.cache_data
def get_company_annual_spend(db_path, ticker: str, refresh_token=0) -> pd.DataFrame:
    """
    Aggregated annual lobbying spend for a ticker, broken out by quarter.
    Sums across all entity names that share the same ticker.

    Returns columns: year, Q1, Q2, Q3, Q4, total, yoy_pct, market_cap,
                     spend_to_mcap_pct
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT year, quarter,
                   SUM(total_lobbying_spend) AS spend,
                   MAX(market_cap)           AS market_cap
            FROM company_lobbying
            WHERE ticker = ?
            GROUP BY year, quarter
            ORDER BY year, quarter
            """,
            conn,
            params=(ticker,),
        )
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame()

    pivot = df.pivot_table(
        index="year", columns="quarter", values="spend", aggfunc="sum"
    ).reset_index()
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        if q not in pivot.columns:
            pivot[q] = 0.0
    pivot = pivot.fillna(0.0)
    pivot["total"] = pivot[["Q1", "Q2", "Q3", "Q4"]].sum(axis=1)

    mcap_yr = df.groupby("year")["market_cap"].max().reset_index()
    pivot = pivot.merge(mcap_yr, on="year", how="left")
    pivot["spend_to_mcap_pct"] = pivot.apply(
        lambda r: r["total"] / r["market_cap"] * 100
        if pd.notna(r["market_cap"]) and r["market_cap"] > 0
        else None,
        axis=1,
    )

    pivot = pivot.sort_values("year").reset_index(drop=True)
    pivot["yoy_pct"] = pivot["total"].pct_change().multiply(100).round(1)
    pivot["yoy_pct"] = pivot["yoy_pct"].where(pivot["yoy_pct"].notna(), other=None)

    return pivot


@st.cache_data
def get_company_stock_snapshots(db_path, ticker: str, refresh_token=0) -> pd.DataFrame:
    """
    Year-end (closest to Dec 31) stock snapshots from stock_performance.
    Returns: year, close_price, return_1y
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY CAST(strftime('%Y', date) AS INTEGER)
                           ORDER BY ABS(JULIANDAY(date)
                                        - JULIANDAY(strftime('%Y', date) || '-12-31'))
                       ) AS rn
                FROM stock_performance
                WHERE ticker = ?
            )
            SELECT
                CAST(strftime('%Y', date) AS INTEGER) AS year,
                close_price,
                return_1y
            FROM ranked
            WHERE rn = 1
            ORDER BY year
            """,
            conn,
            params=(ticker,),
        )
    finally:
        conn.close()

    return df


@st.cache_data
def get_company_entity_names(db_path, ticker: str, refresh_token=0) -> list:
    """Return all company_name values associated with a ticker."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT DISTINCT company_name FROM company_lobbying WHERE ticker = ?",
            conn,
            params=(ticker,),
        )
    finally:
        conn.close()
    return df["company_name"].tolist() if not df.empty else []


@st.cache_data
def get_company_lobbyist_firms(
    db_path, entity_names_key: str, refresh_token=0
) -> pd.DataFrame:
    """
    Per-year count of unique external lobbying firms (registrants) hired,
    and total $ paid to them.  entity_names_key is a pipe-joined string of
    entity names (used as a stable cache key).

    Returns: year, n_firms, total_paid
    """
    import sqlite3

    entity_names = [n for n in entity_names_key.split("|") if n]
    if not entity_names:
        return pd.DataFrame()

    ph = ",".join("?" for _ in entity_names)
    params = [n.upper() for n in entity_names]

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            f"""
            SELECT
                year,
                COUNT(DISTINCT registrant_name) AS n_firms,
                SUM(amount)                     AS total_paid
            FROM lobbying_filings
            WHERE UPPER(client_name) IN ({ph})
              AND amount > 0
            GROUP BY year
            ORDER BY year
            """,
            conn,
            params=params,
        )
    finally:
        conn.close()

    return df


@st.cache_data
def get_company_sector_peers(
    db_path, sector: str, ticker: str, refresh_token=0
) -> pd.DataFrame:
    """
    Annual total lobbying spend for the top 8 companies (by total spend)
    in the same sector, for peer comparison.
    Returns: ticker, year, total (long format)
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            WITH top_tickers AS (
                SELECT ticker
                FROM company_lobbying
                WHERE sector = ? AND ticker IS NOT NULL AND ticker != ''
                GROUP BY ticker
                ORDER BY SUM(total_lobbying_spend) DESC
                LIMIT 8
            )
            SELECT cl.ticker, cl.year, SUM(cl.total_lobbying_spend) AS total
            FROM company_lobbying cl
            JOIN top_tickers tt ON cl.ticker = tt.ticker
            GROUP BY cl.ticker, cl.year
            ORDER BY cl.year, total DESC
            """,
            conn,
            params=(sector,),
        )
    finally:
        conn.close()

    return df
