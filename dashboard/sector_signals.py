"""
Sector rotation, sector spend by year, and new-entrant signal backtests.
"""

import pandas as pd
import streamlit as st



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
