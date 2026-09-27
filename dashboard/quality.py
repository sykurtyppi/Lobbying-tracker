"""
Fundamental quality metrics (ROE, D/E, earnings growth) fetched via yfinance.
"""

import sqlite3
from datetime import datetime

import pandas as pd
import streamlit as st

import config


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL 3 — QUALITY FILTER
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_quality_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ticker_quality_metrics (
            ticker           TEXT PRIMARY KEY,
            roe              REAL,
            debt_to_equity   REAL,
            earnings_growth  REAL,
            fetched_at       TEXT
        )
        """
    )
    conn.commit()


def fetch_quality_metrics_for_tickers(tickers: list[str], db_path: str) -> pd.DataFrame:
    """
    Fetch ROE, debt-to-equity, and earnings growth from yfinance for the
    supplied list of tickers.  Results are upserted into ticker_quality_metrics
    and the full updated table is returned.

    Called on-demand from the UI (not cached directly — the results land in DB
    and are read back via get_quality_metrics_from_db).
    """
    import sqlite3
    import yfinance as yf

    conn = sqlite3.connect(db_path)
    try:
        _ensure_quality_table(conn)
        if not bool(
            getattr(
                config,
                "YFINANCE_REALTIME_MCAP_ENABLED",
                getattr(config, "YFINANCE_ENABLED", True),
            )
        ):
            return pd.read_sql_query(
                "SELECT * FROM ticker_quality_metrics ORDER BY ticker", conn
            )

        today_str = datetime.now().strftime("%Y-%m-%d")

        rows = []
        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).info
                rows.append({
                    "ticker": ticker,
                    "roe": info.get("returnOnEquity"),
                    "debt_to_equity": info.get("debtToEquity"),
                    "earnings_growth": info.get("earningsGrowth"),
                    "fetched_at": today_str,
                })
            except Exception:
                rows.append({
                    "ticker": ticker,
                    "roe": None,
                    "debt_to_equity": None,
                    "earnings_growth": None,
                    "fetched_at": today_str,
                })

        if rows:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO ticker_quality_metrics
                        (ticker, roe, debt_to_equity, earnings_growth, fetched_at)
                    VALUES (:ticker, :roe, :debt_to_equity, :earnings_growth, :fetched_at)
                    ON CONFLICT(ticker) DO UPDATE SET
                        roe            = excluded.roe,
                        debt_to_equity = excluded.debt_to_equity,
                        earnings_growth = excluded.earnings_growth,
                        fetched_at     = excluded.fetched_at
                    """,
                    row,
                )
            conn.commit()

        return pd.read_sql_query("SELECT * FROM ticker_quality_metrics ORDER BY ticker", conn)
    finally:
        conn.close()


@st.cache_data
def get_quality_metrics_from_db(db_path, refresh_token=0) -> pd.DataFrame:
    """Read whatever quality metrics are already stored in the DB."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        _ensure_quality_table(conn)
        return pd.read_sql_query(
            "SELECT * FROM ticker_quality_metrics ORDER BY ticker", conn
        )
    finally:
        conn.close()
