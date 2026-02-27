"""
US Lobbying Equities Strategy - Professional Dashboard
Institutional-grade tool for tracking corporate lobbying expenditures
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import sqlite3
from datetime import datetime, timedelta, timezone
import math
import numpy as np
import sys
import os
import time
import json

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

import config
from data_fetcher import LobbyingDataFetcher


_APP_CSS = """
    <style>
    /* Main background */
    .stApp {
        background-color: #0e1117;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #1a1d24;
    }

    /* Metrics */
    [data-testid="stMetricValue"] {
        color: #ffffff;
        font-size: 28px;
        font-weight: 600;
    }

    [data-testid="stMetricLabel"] {
        color: #a0a0a0;
        font-size: 14px;
    }

    /* Headers */
    h1 {
        color: #ffffff;
        font-weight: 700;
        padding-bottom: 10px;
        border-bottom: 2px solid #2d3748;
    }

    h2, h3 {
        color: #e0e0e0;
        font-weight: 600;
    }

    /* Tables */
    .dataframe {
        font-size: 13px;
    }

    .dataframe thead tr th {
        background-color: #1a1d24 !important;
        color: #ffffff !important;
        font-weight: 600;
    }

    .dataframe tbody tr:hover {
        background-color: #2d3748 !important;
    }

    /* Buttons */
    .stButton > button {
        background-color: #2d3748;
        color: #ffffff;
        border: 1px solid #4a5568;
        font-weight: 500;
    }

    .stButton > button:hover {
        background-color: #4a5568;
        border-color: #718096;
    }

    /* Remove default padding */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 0rem;
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }

    .stTabs [data-baseweb="tab"] {
        background-color: #1a1d24;
        color: #a0a0a0;
        border-radius: 4px 4px 0 0;
        padding: 10px 20px;
        font-weight: 500;
    }

    .stTabs [aria-selected="true"] {
        background-color: #2d3748;
        color: #ffffff;
    }

    /* Expander */
    .streamlit-expanderHeader {
        background-color: #1a1d24;
        color: #ffffff;
        font-weight: 500;
    }
    </style>
"""


def _configure_streamlit_page() -> None:
    """Apply page config and dashboard CSS only during UI runtime."""
    st.set_page_config(
        page_title="US Lobbying Equities Strategy",
        page_icon="",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(_APP_CSS, unsafe_allow_html=True)


_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "data", "app_settings.json")

# JSON key → (config attribute name, type coercion)
_SETTINGS_CONFIG_MAP: dict[str, tuple[str, type]] = {
    "senate_api_endpoint":        ("SENATE_API_BASE",              str),
    "senate_auto_update":         ("SENATE_AUTO_UPDATE",            bool),
    "senate_fetch_interval_hours":("SENATE_UPDATE_INTERVAL_HOURS",  int),
    "lda_filing_lag_days":        ("LDA_FILING_LAG_DAYS",           int),
    "sec_universe_auto_sync":     ("SEC_UNIVERSE_AUTO_SYNC",        bool),
    "sec_universe_refresh_days":  ("SEC_UNIVERSE_REFRESH_DAYS",     int),
    "sec_user_agent":             ("SEC_USER_AGENT",                str),
    "opensecrets_api_key":        ("OPENSECRETS_API_KEY",           str),
    "opensecrets_enabled":        ("OPENSECRETS_ENABLED",           bool),
    "yfinance_realtime_mcap":     ("YFINANCE_REALTIME_MCAP_ENABLED", bool),
    "yfinance_price_history":     ("YFINANCE_PRICE_HISTORY_ENABLED",  bool),
    "yfinance_history_years":     ("YFINANCE_HISTORY_YEARS",        int),
}


def _config_defaults() -> dict:
    """Build settings defaults from config.py — config.py is the single source of truth."""
    yf_realtime_enabled = bool(
        getattr(config, "YFINANCE_REALTIME_MCAP_ENABLED", config.YFINANCE_ENABLED)
    )
    yf_price_history_enabled = bool(
        getattr(config, "YFINANCE_PRICE_HISTORY_ENABLED", config.YFINANCE_ENABLED)
    )
    return {
        "senate_api_endpoint":        config.SENATE_API_BASE,
        "senate_auto_update":         config.SENATE_AUTO_UPDATE,
        "senate_fetch_interval_hours": config.SENATE_UPDATE_INTERVAL_HOURS,
        "lda_filing_lag_days":        config.LDA_FILING_LAG_DAYS,
        "sec_universe_auto_sync":     config.SEC_UNIVERSE_AUTO_SYNC,
        "sec_universe_refresh_days":  config.SEC_UNIVERSE_REFRESH_DAYS,
        "sec_user_agent":             config.SEC_USER_AGENT,
        "opensecrets_api_key":        config.OPENSECRETS_API_KEY,
        "opensecrets_enabled":        config.OPENSECRETS_ENABLED,
        "yfinance_realtime_mcap":     yf_realtime_enabled,
        "yfinance_price_history":     yf_price_history_enabled,
        "yfinance_history_years":     config.YFINANCE_HISTORY_YEARS,
        "custom_ticker_mappings_csv": "",
    }


def load_settings() -> dict:
    """Load persisted settings from JSON file, falling back to config.py defaults."""
    defaults = _config_defaults()
    if os.path.exists(_SETTINGS_PATH):
        try:
            with open(_SETTINGS_PATH, "r") as f:
                saved = json.load(f)
            # Forward-compat merge: config.py defaults for any key not yet in file
            merged = dict(defaults)
            merged.update(saved)
            return merged
        except Exception:
            pass
    return defaults


def apply_settings_to_config(settings: dict) -> None:
    """
    Push user-saved settings back into the live config module so every part
    of the app that reads config constants gets the user-overridden values.
    Called at startup (after load_settings) and after every Save.
    """
    for json_key, (cfg_attr, cast) in _SETTINGS_CONFIG_MAP.items():
        if json_key in settings:
            try:
                setattr(config, cfg_attr, cast(settings[json_key]))
            except (TypeError, ValueError):
                pass


def save_settings(settings: dict) -> bool:
    """Persist settings dict to JSON file and apply them to config. Returns True on success."""
    try:
        os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
        apply_settings_to_config(settings)
        return True
    except Exception as e:
        st.error(f"Could not save settings: {e}")
        return False


def apply_custom_ticker_mappings(csv_text: str, db_path: str) -> tuple[int, list[str]]:
    """
    Parse CSV text (Company Name,TICKER) and persist verified aliases to DB.
    Returns (rows_upserted, list_of_errors).
    """
    import csv
    import io
    import sqlite3

    rows = []
    errors = []

    reader = csv.reader(io.StringIO(csv_text.strip()))
    for i, parts in enumerate(reader, start=1):
        if not parts or all(not str(p).strip() for p in parts):
            continue

        first = str(parts[0]).strip()
        if i == 1 and first.lower() in {"company", "company name", "alias", "alias_name"}:
            continue

        if len(parts) < 2:
            errors.append(f"Line {i}: expected 'Company Name,TICKER'")
            continue

        alias_name = str(parts[0]).strip()
        ticker = str(parts[1]).strip().upper()
        canonical_name = str(parts[2]).strip() if len(parts) > 2 and str(parts[2]).strip() else None

        if not alias_name or not ticker:
            errors.append(f"Line {i}: empty company or ticker")
            continue
        rows.append((alias_name, canonical_name, ticker))

    if not rows:
        return 0, errors

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias_name TEXT UNIQUE,
                canonical_name TEXT,
                ticker TEXT,
                confidence REAL,
                status TEXT DEFAULT 'verified',
                source TEXT,
                notes TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        cursor.executemany(
            """
            INSERT INTO entity_aliases
            (alias_name, canonical_name, ticker, confidence, status, source, notes, created_at, updated_at)
            VALUES (?, ?, ?, 1.0, 'verified', 'manual_ui', 'imported from settings', ?, ?)
            ON CONFLICT(alias_name) DO UPDATE SET
                canonical_name = COALESCE(excluded.canonical_name, entity_aliases.canonical_name),
                ticker = excluded.ticker,
                confidence = 1.0,
                status = 'verified',
                source = 'manual_ui',
                notes = 'imported from settings',
                updated_at = excluded.updated_at
            """,
            [(alias, canonical, ticker, now_iso, now_iso) for alias, canonical, ticker in rows],
        )
        conn.commit()
    finally:
        conn.close()

    return len(rows), errors


# Initialize data fetcher
@st.cache_resource
def get_data_fetcher():
    import os
    db_path = os.path.join(os.path.dirname(__file__), "data", "lobbying_data.db")
    return LobbyingDataFetcher(db_path=db_path)

@st.cache_data
def get_available_years(db_path, refresh_token=0):
    """Get years that actually have data in the database"""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        try:
            years_df = pd.read_sql_query(
                "SELECT DISTINCT year FROM company_lobbying ORDER BY year DESC",
                conn,
            )
        finally:
            conn.close()
        years = (
            pd.to_numeric(years_df["year"], errors="coerce")
            .dropna()
            .astype(int)
            .tolist()
        )
        return years if years else [datetime.now().year - 1]  # Fallback to last year
    except Exception as e:
        print(f"Error getting years: {e}")
        return [datetime.now().year - 1]  # Fallback


@st.cache_data
def get_default_year(db_path, refresh_token=0):
    """
    Prefer the latest complete year (all four quarters present).
    Falls back to the latest available year if completeness metadata is absent.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        complete_df = pd.read_sql_query(
            """
            SELECT year
            FROM (
                SELECT year, COUNT(DISTINCT quarter) AS n_quarters
                FROM company_lobbying
                GROUP BY year
            )
            WHERE n_quarters = 4
            ORDER BY year DESC
            LIMIT 1
            """,
            conn,
        )
        if not complete_df.empty:
            return int(complete_df.iloc[0]["year"])

        latest_df = pd.read_sql_query(
            "SELECT MAX(year) AS year FROM company_lobbying",
            conn,
        )
        if not latest_df.empty and pd.notna(latest_df.iloc[0]["year"]):
            return int(latest_df.iloc[0]["year"])
    finally:
        conn.close()

    return datetime.now().year - 1


@st.cache_data
def get_available_sectors(db_path, refresh_token=0):
    """Get distinct sector values that exist in the database (Yahoo Finance names)."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        try:
            sectors_df = pd.read_sql_query(
                """SELECT DISTINCT sector FROM company_lobbying
                   WHERE sector IS NOT NULL AND sector != ''
                   ORDER BY sector""",
                conn,
            )
        finally:
            conn.close()
        return sectors_df["sector"].tolist()
    except Exception as e:
        print(f"Error getting sectors: {e}")
        return []


@st.cache_data
def get_latest_ingestion_status(db_path, refresh_token=0):
    """
    Return latest ingestion status for every year present in company_lobbying.

    Years with no ingestion audit row (legacy/pre-audit snapshots) are included
    with status='legacy_snapshot' and null run fields.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        years_df = pd.read_sql_query(
            "SELECT DISTINCT year FROM company_lobbying ORDER BY year DESC",
            conn,
        )
        if not years_df.empty:
            years_df["year"] = pd.to_numeric(years_df["year"], errors="coerce")
            years_df = years_df.dropna(subset=["year"]).copy()
            years_df["year"] = years_df["year"].astype(int)
        if years_df.empty:
            return pd.DataFrame()

        table_exists = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_runs'",
            conn,
        )
        if table_exists.empty:
            years_df["status"] = "legacy_snapshot"
            years_df["audit_state"] = "legacy_snapshot"
            years_df["is_complete"] = pd.NA
            years_df["api_reported_count"] = pd.NA
            years_df["fetched_count"] = pd.NA
            years_df["filtered_count"] = pd.NA
            years_df["inserted_raw_count"] = pd.NA
            years_df["aggregate_rows"] = pd.NA
            years_df["started_at"] = pd.NA
            years_df["finished_at"] = pd.NA
            years_df["latest_attempt_status"] = pd.NA
            years_df["latest_attempt_started_at"] = pd.NA
            years_df["latest_attempt_notes"] = pd.NA
            years_df["notes"] = "No ingestion_runs table found for this snapshot."
            return years_df

        latest_runs = pd.read_sql_query(
            """
            WITH latest_attempt AS (
                SELECT year, status, started_at, notes
                FROM (
                    SELECT
                        r.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY r.year
                            ORDER BY r.started_at DESC, r.id DESC
                        ) AS rn
                    FROM ingestion_runs r
                )
                WHERE rn = 1
            ),
            latest_snapshot AS (
                SELECT
                    year,
                    status,
                    is_complete,
                    api_reported_count,
                    fetched_count,
                    filtered_count,
                    inserted_raw_count,
                    aggregate_rows,
                    started_at,
                    finished_at,
                    notes
                FROM (
                    SELECT
                        r.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY r.year
                            ORDER BY r.started_at DESC, r.id DESC
                        ) AS rn
                    FROM ingestion_runs r
                    WHERE r.status IN ('complete', 'partial_success')
                )
                WHERE rn = 1
            )
            SELECT
                a.year,
                COALESCE(s.status, a.status) AS status,
                CASE
                    WHEN s.is_complete IS NOT NULL THEN s.is_complete
                    WHEN a.status = 'legacy_snapshot' THEN NULL
                    WHEN a.status IS NOT NULL THEN 0
                    ELSE NULL
                END AS is_complete,
                s.api_reported_count,
                s.fetched_count,
                s.filtered_count,
                s.inserted_raw_count,
                s.aggregate_rows,
                s.started_at,
                s.finished_at,
                COALESCE(s.notes, a.notes) AS notes,
                a.status AS latest_attempt_status,
                a.started_at AS latest_attempt_started_at,
                a.notes AS latest_attempt_notes,
                CASE
                    WHEN s.year IS NOT NULL THEN 'snapshot'
                    WHEN a.year IS NOT NULL THEN 'attempt_only'
                    ELSE 'legacy_snapshot'
                END AS audit_state
            FROM latest_attempt a
            LEFT JOIN latest_snapshot s ON a.year = s.year
            """,
            conn,
        )
    finally:
        conn.close()

    merged = years_df.merge(latest_runs, on="year", how="left")
    if not merged.empty:
        merged["year"] = pd.to_numeric(merged["year"], errors="coerce")
        merged = merged.dropna(subset=["year"]).copy()
        merged["year"] = merged["year"].astype(int)
    merged["status"] = merged["status"].fillna("legacy_snapshot")
    merged["audit_state"] = merged["audit_state"].fillna("legacy_snapshot")
    merged["notes"] = merged["notes"].fillna(
        "No ingestion audit row for this year (likely loaded before ingestion tracking)."
    )
    merged = merged.sort_values("year", ascending=False).reset_index(drop=True)
    return merged


@st.cache_data
def get_alias_review_queue(db_path, refresh_token=0, limit=200):
    """
    Highest-impact unmatched company aliases to review and map manually.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        table_exists = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_aliases'",
            conn,
        )
        if table_exists.empty:
            df = pd.read_sql_query(
                """
                SELECT
                    TRIM(cl.company_name) AS alias_name,
                    MIN(cl.year) AS first_year,
                    MAX(cl.year) AS latest_year,
                    COUNT(DISTINCT cl.year) AS years_present,
                    SUM(cl.total_lobbying_spend) AS total_spend,
                    COUNT(*) AS row_count,
                    'unreviewed' AS review_status,
                    NULL AS reviewed_ticker
                FROM company_lobbying cl
                WHERE (cl.ticker IS NULL OR TRIM(cl.ticker) = '')
                  AND cl.company_name IS NOT NULL
                  AND TRIM(cl.company_name) != ''
                GROUP BY TRIM(cl.company_name)
                ORDER BY total_spend DESC
                LIMIT ?
                """,
                conn,
                params=(int(limit),),
            )
        else:
            df = pd.read_sql_query(
                """
                SELECT
                    TRIM(cl.company_name) AS alias_name,
                    MIN(cl.year) AS first_year,
                    MAX(cl.year) AS latest_year,
                    COUNT(DISTINCT cl.year) AS years_present,
                    SUM(cl.total_lobbying_spend) AS total_spend,
                    COUNT(*) AS row_count,
                    COALESCE(MAX(ea.status), 'unreviewed') AS review_status,
                    MAX(ea.ticker) AS reviewed_ticker
                FROM company_lobbying cl
                LEFT JOIN entity_aliases ea
                  ON UPPER(TRIM(cl.company_name)) = UPPER(TRIM(ea.alias_name))
                WHERE (cl.ticker IS NULL OR TRIM(cl.ticker) = '')
                  AND cl.company_name IS NOT NULL
                  AND TRIM(cl.company_name) != ''
                GROUP BY TRIM(cl.company_name)
                ORDER BY total_spend DESC
                LIMIT ?
                """,
                conn,
                params=(int(limit),),
            )
    finally:
        conn.close()

    if df.empty:
        return df

    # Suggest likely mappings in-UI to speed manual review.
    try:
        from data_fetcher import CompanyMapper
    except ImportError:
        try:
            from src.data_fetcher import CompanyMapper
        except ImportError:
            CompanyMapper = None

    if CompanyMapper is not None:
        mapper = CompanyMapper(db_path=db_path)
        df["likely_non_public"] = df["alias_name"].apply(
            CompanyMapper.is_likely_non_public_entity
        )
        needs_suggestion = (
            df["reviewed_ticker"].fillna("").astype(str).str.strip() == ""
        )
        df["suggested_ticker"] = None
        df["suggested_method"] = None
        if needs_suggestion.any():
            suggested = df.loc[needs_suggestion, "alias_name"].apply(
                mapper.find_ticker_with_info
            )
            df.loc[needs_suggestion, "suggested_ticker"] = suggested.apply(
                lambda x: x[0] if x else None
            )
            df.loc[needs_suggestion, "suggested_method"] = suggested.apply(
                lambda x: x[1] if x else None
            )
    else:
        df["likely_non_public"] = False
        df["suggested_ticker"] = None
        df["suggested_method"] = None

    df["first_year"] = pd.to_numeric(df["first_year"], errors="coerce").astype("Int64")
    df["latest_year"] = pd.to_numeric(df["latest_year"], errors="coerce").astype("Int64")
    df["years_present"] = pd.to_numeric(df["years_present"], errors="coerce").astype("Int64")
    df = df.sort_values("total_spend", ascending=False).reset_index(drop=True)
    return df


@st.cache_data
def get_nonpositive_spend_rows(db_path, refresh_token=0):
    """Rows in company_lobbying with invalid non-positive spend values."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                year,
                COUNT(*) AS bad_rows
            FROM company_lobbying
            WHERE total_lobbying_spend IS NULL
               OR total_lobbying_spend <= 0
            GROUP BY year
            ORDER BY year DESC
            """,
            conn,
        )
    finally:
        conn.close()

    if not df.empty:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
        df["bad_rows"] = pd.to_numeric(df["bad_rows"], errors="coerce").fillna(0).astype(int)
    return df


def format_currency(value):
    """Format number as currency"""
    if pd.isna(value):
        return "N/A"
    if value >= 1e9:
        return f"${value/1e9:.2f}B"
    elif value >= 1e6:
        return f"${value/1e6:.2f}M"
    elif value >= 1e3:
        return f"${value/1e3:.2f}K"
    else:
        return f"${value:.0f}"


def format_percentage(value):
    """Format number as percentage"""
    if pd.isna(value):
        return "N/A"
    return f"{value:.2f}%"


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
    Spend/MCap    (0-30 pts)  – percentile rank of spend-to-market-cap ratio within
                               the scored universe.  Companies without market-cap data
                               score 0 (not the old 10-pt free bonus).
    Consistency   (0-30 pts)  – proportion of the year's 4 quarters with filings.

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

    # ── Spend/MCap component (0-30 pts) — percentile rank ────────────────────
    # Companies WITHOUT market-cap data score 0 (was 10).
    # Giving untickered private companies a free 10 pts polluted the rankings.
    def _mcap_pts(series):
        """Rank each company's spend/mcap ratio within the scored universe."""
        notna = series.notna()
        result = pd.Series(0.0, index=series.index)   # 0 for no market-cap data
        if notna.sum() > 0:
            ranks = series[notna].rank(pct=True)
            result[notna] = (ranks * 30.0).round(1)
        return result

    # ── Consistency component (0-30 pts) ─────────────────────────────────────
    def _consistency_pts(q):
        return round((min(int(q), 4) / 4.0) * 30.0, 1)

    df["yoy_pts"]         = df["yoy_pct"].apply(_yoy_pts)
    df["mcap_pts"]        = _mcap_pts(df["avg_spend_mcap"])
    df["consistency_pts"] = df["quarters_count"].apply(_consistency_pts)
    df["conviction_score"] = (
        df["yoy_pts"] + df["mcap_pts"] + df["consistency_pts"]
    ).round(1)

    return df.sort_values("conviction_score", ascending=False).reset_index(drop=True)


@st.cache_data
def get_signal_returns(db_path, year, refresh_token=0):
    """
    Join company_lobbying Q4 with stock_performance to show forward returns
    for top lobbying companies in `year`.

    Uses a ±46-day window around the lag-adjusted Q4 signal date and
    picks the single stored snapshot *closest* to that signal date per ticker via a
    window function.  Does NOT require return_1y so recent years with only
    partial forward data are still included.

    Returns empty DataFrame if stock_performance table has no rows yet.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        # Guard: nothing to join against yet
        chk = pd.read_sql_query(
            "SELECT COUNT(*) AS n FROM stock_performance", conn
        )
        if chk["n"].iloc[0] == 0:
            return pd.DataFrame()

        # Lag-adjusted signal window around Q4 filing availability date.
        ref_date, date_lo, date_hi = _q4_signal_window(int(year), window_days=46)

        df = pd.read_sql_query(
            """
            WITH ranked_sp AS (
                /* For each ticker keep only the snapshot closest to Dec 31 */
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
                /* Aggregate by raw filing entity first */
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
                /* Then collapse to one representative row per ticker */
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
            /* No return_1y filter — include companies even when 1y data
               is not yet available (e.g. current or most-recent year). */
            ORDER BY cl.total_spend DESC
            """,
            conn,
            params={"year": year, "ref_date": ref_date,
                    "date_lo": date_lo, "date_hi": date_hi},
        )
    finally:
        conn.close()

    return df


def _fetch_spx_annual_returns(min_year: int, max_year: int) -> tuple[dict, str | None]:
    """
    Return (spx_dict, error_or_None) where spx_dict maps year → calendar-year
    total return (%), using a fixed hardcoded benchmark table.

    This intentionally avoids live yfinance fetches for benchmark stability
    and reproducibility across runs.
    """
    _SPX_KNOWN_TOTAL_RETURN: dict[int, float] = {
        2019: 31.49,
        2020: 18.40,
        2021: 28.71,
        2022: -18.11,
        2023: 26.29,
        2024: 25.02,
        2025: 17.88,
    }

    spx_dict = {
        yr: _SPX_KNOWN_TOTAL_RETURN[yr]
        for yr in range(min_year, max_year + 1)
        if yr in _SPX_KNOWN_TOTAL_RETURN
    }
    return spx_dict, None


def _sanitize_ticker_list(tickers) -> list[str]:
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
    """
    Convert turnover% and bps-per-100%-turnover into return percentage points.
    Example: 50% turnover with 10 bps cost => 0.05% return drag.
    """
    if turnover_pct is None or pd.isna(turnover_pct):
        return 0.0
    return round((float(turnover_pct) / 100.0) * (float(cost_bps) / 100.0), 4)


def _q4_signal_window(year: int, window_days: int = 46) -> tuple[str, str, str]:
    """
    Build a lag-adjusted Q4 signal window.
    Returns (ref_date, date_lo, date_hi) as YYYY-MM-DD strings.
    """
    lag_days = max(int(getattr(config, "LDA_FILING_LAG_DAYS", 20) or 0), 0)
    signal_dt = pd.Timestamp(year=int(year), month=12, day=31) + pd.Timedelta(days=lag_days)
    date_lo = (signal_dt - pd.Timedelta(days=window_days)).strftime("%Y-%m-%d")
    date_hi = (signal_dt + pd.Timedelta(days=window_days)).strftime("%Y-%m-%d")
    return signal_dt.strftime("%Y-%m-%d"), date_lo, date_hi


def _fetch_spx_returns_for_signal_years(
    signal_years,
    prefer_forward_window: bool = True,
    window_days: int = 46,
) -> tuple[dict, str | None]:
    """
    Return S&P benchmark returns mapped by hold year (signal_year + 1).

    When possible, computes forward-window returns aligned to the same Q4+lag
    anchor used by strategy returns:
      ref = signal_year Q4 end + filing lag
      fwd = return to ref + 365 days (next available trading day).

    Falls back to hardcoded calendar-year totals when live index history is
    unavailable or incomplete.
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


@st.cache_data
def get_benchmark_comparison(db_path, refresh_token=0):
    """
    Build a year-by-year table comparing the equal-weight lobbying strategy
    return against the S&P 500 return on a hold-year aligned basis.

    Strategy return: equal-weight average 1-year forward return for all companies
    with Q4 lobbying spend >= $1M, measured from a lag-adjusted Q4 signal
    snapshot of `signal_year`. This return is realized during
    `hold_year = signal_year + 1`.
    Benchmark return: S&P 500 return for that same hold year.

    Only years where >= 3 companies have non-null 1y return data are included.
    Returns an empty DataFrame if stock_performance is not yet populated.
    The returned DataFrame includes a '_spx_error' column (str or None) that surfaces
    any yfinance failure so the UI can show a warning instead of silently hiding bars.
    """
    import sqlite3

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
                params={"year": year, "ref_date": ref_date,
                        "date_lo": date_lo, "date_hi": date_hi},
            )
            if df_yr.empty:
                continue

            valid_df = df_yr[df_yr["return_1y"].notna()].copy()
            mapped_n = int(len(df_yr))
            valid_n = int(len(valid_df))
            return_cov = (valid_n / mapped_n * 100.0) if mapped_n > 0 else 0.0

            if valid_n >= 3 and return_cov >= min_return_cov:
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
                        SUM(entity_spend) AS universe_spend,
                        SUM(CASE WHEN has_ticker = 1 THEN 1 ELSE 0 END) AS mapped_entities,
                        SUM(CASE WHEN has_ticker = 1 THEN entity_spend ELSE 0 END) AS mapped_spend
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

                rows.append({
                    "signal_year": signal_year,
                    "year": signal_year + 1,  # hold year
                    "strategy_return_gross": gross_ret,
                    "n_companies": valid_n,
                    "mapped_companies": mapped_n,
                    "return_coverage_pct": round(return_cov, 1),
                    "entity_coverage_pct": round(entity_cov, 1),
                    "spend_coverage_pct": round(spend_cov, 1),
                    "constituents": constituents,
                })
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

    # Suppress error if fallback filled everything
    if spx_error and strat_df["spx_return"].notna().all():
        spx_error = None

    strat_df["_spx_error"] = spx_error
    strat_df = strat_df.drop(columns=["constituents"], errors="ignore")
    return strat_df


@st.cache_data
def get_conviction_benchmark(db_path, top_n: int, refresh_token=0):
    """
    Equal-weight 1-year forward return for the top-N companies by annual
    conviction score, benchmarked against S&P 500.

    Works year-by-year: for each year that has stock_performance data, score
    all public companies, take the top_n by conviction_score, look up their
    1-year forward return from the lag-adjusted Q4 signal snapshot, and
    compute the
    equal-weight average.  Requires at least 3 companies with return_1y data.

    Returns the same schema as get_benchmark_comparison():
        signal_year, year(hold_year), strategy_return, n_companies, spx_return, _spx_error
    """
    import sqlite3

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
    finally:
        conn.close()

    rows = []
    for year in years_df["year"].tolist():
        yr = int(year)

        # Conviction scores for this year (public companies, ≥$1M spend)
        scores = get_conviction_scores(db_path, yr, refresh_token=refresh_token)
        if scores.empty:
            continue

        top_tickers = (
            scores.nlargest(top_n, "conviction_score")["ticker"]
            .dropna()
            .astype(str)
            .str.strip()
            .tolist()
        )
        top_tickers = _sanitize_ticker_list(top_tickers)
        selected_n = len(top_tickers)
        if selected_n < 3:
            continue

        # 1-year forward returns for those tickers from the lag-adjusted
        # Q4 signal snapshot
        conn2 = sqlite3.connect(db_path)
        try:
            placeholders = ",".join(["?" for _ in top_tickers])
            ref_date, date_lo, date_hi = _q4_signal_window(int(yr), window_days=46)
            df_yr = pd.read_sql_query(
                f"""
                WITH ranked_sp AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY ticker
                               ORDER BY ABS(JULIANDAY(date) - JULIANDAY(?))
                           ) AS rn
                    FROM stock_performance
                    WHERE date BETWEEN ? AND ?
                      AND ticker IN ({placeholders})
                ),
                nearest_sp AS (SELECT * FROM ranked_sp WHERE rn = 1)
                SELECT ticker, return_1y
                FROM nearest_sp
                """,
                conn2,
                params=[ref_date, date_lo, date_hi] + top_tickers,
            )
        finally:
            conn2.close()

        if df_yr.empty:
            continue

        valid_df = df_yr[df_yr["return_1y"].notna()].copy()
        valid_n = int(len(valid_df))
        return_cov = (valid_n / selected_n * 100.0) if selected_n > 0 else 0.0

        if valid_n >= 3 and return_cov >= min_return_cov:
            gross_ret = round(float(valid_df["return_1y"].mean()), 2)
            rows.append({
                "signal_year":     yr,
                "year":            yr + 1,  # hold year
                "strategy_return_gross": gross_ret,
                "n_companies":     valid_n,
                "selected_companies": selected_n,
                "return_coverage_pct": round(return_cov, 1),
                "constituents": _sanitize_ticker_list(valid_df["ticker"].tolist()),
            })

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


@st.cache_data
def get_database_stats(db_path, refresh_token=0):
    """Return quick database health stats for Settings tab."""
    import sqlite3

    stats = {
        "total_records": 0,
        "last_updated": None,
        "db_size_mb": 0.0,
    }

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM company_lobbying")
        stats["total_records"] = cursor.fetchone()[0]
        cursor.execute("SELECT MAX(last_updated) FROM company_lobbying")
        stats["last_updated"] = cursor.fetchone()[0]
    finally:
        conn.close()

    if os.path.exists(db_path):
        stats["db_size_mb"] = os.path.getsize(db_path) / (1024 * 1024)

    return stats


@st.cache_data
def get_company_lobbying_export_bytes(db_path, refresh_token=0):
    """Build a CSV export payload for company_lobbying."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        export_df = pd.read_sql_query(
            "SELECT * FROM company_lobbying ORDER BY year DESC, total_lobbying_spend DESC",
            conn,
        )
    finally:
        conn.close()
    return export_df.to_csv(index=False).encode("utf-8")


@st.cache_data
def get_sec_universe_stats(db_path, refresh_token=0) -> dict:
    """Return SEC ticker-universe cache health stats."""
    import sqlite3

    stats = {
        "rows": 0,
        "distinct_tickers": 0,
        "last_fetched_at": None,
        "etf_like_rows": 0,
        "distinct_exchanges": 0,
    }

    conn = sqlite3.connect(db_path)
    try:
        exists = pd.read_sql_query(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name='sec_ticker_universe'
            """,
            conn,
        )
        if exists.empty:
            return stats

        row = pd.read_sql_query(
            """
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT ticker) AS distinct_tickers,
                MAX(fetched_at) AS last_fetched_at,
                SUM(CASE WHEN COALESCE(is_etf, 0) = 1 THEN 1 ELSE 0 END) AS etf_like_rows,
                COUNT(DISTINCT exchange) AS distinct_exchanges
            FROM sec_ticker_universe
            """,
            conn,
        ).iloc[0]
    finally:
        conn.close()

    stats["rows"] = int(row.get("rows") or 0)
    stats["distinct_tickers"] = int(row.get("distinct_tickers") or 0)
    stats["last_fetched_at"] = row.get("last_fetched_at")
    stats["etf_like_rows"] = int(row.get("etf_like_rows") or 0)
    stats["distinct_exchanges"] = int(row.get("distinct_exchanges") or 0)
    return stats


@st.cache_data
def _get_revalidation_preview(db_path, refresh_token=0) -> pd.DataFrame:
    """
    Run revalidate_ticker_mappings in dry-run mode and return a DataFrame of
    proposed changes (nullifications + remaps) decorated with lobbying spend
    so the highest-impact rows surface first.

    Returns a DataFrame with columns:
        Company | Current Ticker | New Ticker | Action | Total Spend ($) | Rows | Latest Year
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        existing = pd.read_sql_query(
            """
            SELECT
                cl.company_name,
                cl.ticker                   AS current_ticker,
                SUM(cl.total_lobbying_spend) AS total_spend,
                COUNT(*)                     AS row_count,
                MAX(cl.year)                 AS latest_year
            FROM company_lobbying cl
            WHERE cl.ticker IS NOT NULL AND cl.ticker != ''
            GROUP BY cl.company_name, cl.ticker
            ORDER BY total_spend DESC
            """,
            conn,
        )
    finally:
        conn.close()

    if existing.empty:
        return pd.DataFrame()

    # Import mapper inside the function to avoid import-time Streamlit issues
    try:
        from data_fetcher import CompanyMapper
    except ImportError:
        from src.data_fetcher import CompanyMapper

    mapper = CompanyMapper(db_path=db_path)
    records = []

    for _, row in existing.iterrows():
        company_name   = row["company_name"]
        current_ticker = str(row["current_ticker"]).strip()
        new_ticker, _method = mapper.find_ticker_with_info(company_name)

        if new_ticker is None:
            action     = "NULLIFY"
            new_ticker_display = "—"
        elif new_ticker != current_ticker:
            action     = "REMAP"
            new_ticker_display = new_ticker
        else:
            continue  # unchanged — don't clutter the preview

        records.append({
            "Company":         company_name,
            "Current Ticker":  current_ticker,
            "New Ticker":      new_ticker_display,
            "Action":          action,
            "Total Spend ($)": row["total_spend"],
            "Rows":            int(row["row_count"]),
            "Latest Year":     int(row["latest_year"]),
        })

    if not records:
        return pd.DataFrame(columns=[
            "Company", "Current Ticker", "New Ticker", "Action",
            "Total Spend ($)", "Rows", "Latest Year",
        ])

    preview = pd.DataFrame(records)
    preview = preview.sort_values("Total Spend ($)", ascending=False).reset_index(drop=True)
    preview["Total Spend ($)"] = preview["Total Spend ($)"].apply(format_currency)
    return preview


@st.cache_data
def _get_fuzzy_audit(db_path, refresh_token=0) -> pd.DataFrame:
    """
    Return all company→ticker pairs where match_method = 'fuzzy', ordered by
    total lobbying spend so high-impact false positives surface first.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        # Check column exists first
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(company_lobbying)")
        cols = {row[1] for row in cursor.fetchall()}
        if "match_method" not in cols:
            return pd.DataFrame()

        df = pd.read_sql_query(
            """
            SELECT
                company_name                AS "Company",
                ticker                      AS "Ticker",
                match_method                AS "Method",
                COUNT(*)                    AS "Rows",
                SUM(total_lobbying_spend)   AS "Total Spend ($)",
                MAX(year)                   AS "Latest Year"
            FROM company_lobbying
            WHERE match_method = 'fuzzy'
              AND ticker IS NOT NULL
              AND ticker != ''
            GROUP BY company_name, ticker, match_method
            ORDER BY SUM(total_lobbying_spend) DESC
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return df

    df["Total Spend ($)"] = df["Total Spend ($)"].apply(format_currency)
    return df


@st.cache_data
def _load_opensecrets_contribs(db_path, year, refresh_token=0) -> pd.DataFrame:
    """Load OpenSecrets contribution data for a given year from the DB."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        try:
            # Check table exists before querying
            tbl = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='opensecrets_contribs'",
                conn,
            )
            if tbl.empty:
                return pd.DataFrame()
            df = pd.read_sql_query(
                """
                SELECT company_name, ticker, total_contribs, pacs, indivs,
                       os_lobbying, outside_spend
                FROM opensecrets_contribs
                WHERE year = ?
                ORDER BY (total_contribs IS NULL), total_contribs DESC
                """,
                conn,
                params=(year,),
            )
            return df
        finally:
            conn.close()
    except Exception:
        return pd.DataFrame()


def _exact_binomial_pvalue(n_trials: int, n_wins: int) -> float:
    """One-sided exact binomial p-value under H0: p(win)=0.5."""
    if n_trials <= 0:
        return 1.0
    n_wins = max(0, min(n_wins, n_trials))
    total = 2 ** n_trials
    favorable = sum(math.comb(n_trials, k) for k in range(n_wins, n_trials + 1))
    return favorable / total


def _compute_overfit_diagnostics(
    cmp_df: pd.DataFrame,
    strategy_col: str,
    benchmark_col: str = "spx_return",
) -> dict | None:
    """
    Compute lightweight anti-overfitting diagnostics from annual return series.
    Uses a chronological train/test split plus bootstrap CI on average alpha.
    """
    required_cols = {"year", strategy_col, benchmark_col}
    if not required_cols.issubset(set(cmp_df.columns)):
        return None

    work = cmp_df[["year", strategy_col, benchmark_col]].dropna().copy()
    if len(work) < 4:
        return None

    work = work.sort_values("year").reset_index(drop=True)
    work["alpha"] = work[strategy_col] - work[benchmark_col]
    n_total = len(work)

    n_test = max(2, int(round(n_total * 0.35)))
    n_test = min(n_test, n_total - 2)
    if n_test < 1:
        return None

    train = work.iloc[:-n_test]
    test = work.iloc[-n_test:]
    if len(train) < 2 or len(test) < 1:
        return None

    alpha = work["alpha"].astype(float).to_numpy()
    rng = np.random.default_rng(42)
    boot = rng.choice(alpha, size=(4000, len(alpha)), replace=True).mean(axis=1)
    ci_low, ci_high = np.percentile(boot, [2.5, 97.5])

    hit_total = int((work["alpha"] > 0).sum())
    hit_test = int((test["alpha"] > 0).sum())
    p_binom = _exact_binomial_pvalue(n_total, hit_total)

    train_alpha = float(train["alpha"].mean())
    test_alpha = float(test["alpha"].mean())
    alpha_drift = test_alpha - train_alpha
    avg_alpha = float(work["alpha"].mean())

    if train_alpha > 0 and test_alpha > 0 and ci_low > 0 and p_binom < 0.10:
        verdict = "Robust"
    elif test_alpha < 0 or alpha_drift <= -3.0:
        verdict = "Fragile"
    else:
        verdict = "Needs more data"

    return {
        "n_years": n_total,
        "split": f"{int(train['year'].min())}-{int(train['year'].max())} train / "
                 f"{int(test['year'].min())}-{int(test['year'].max())} test",
        "avg_alpha": avg_alpha,
        "train_alpha": train_alpha,
        "test_alpha": test_alpha,
        "alpha_drift": alpha_drift,
        "hit_rate": float((work["alpha"] > 0).mean() * 100),
        "test_hit_rate": float((test["alpha"] > 0).mean() * 100),
        "p_binom": float(p_binom),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "verdict": verdict,
    }


def _compute_return_risk_stats(
    cmp_df: pd.DataFrame,
    strategy_col: str,
    benchmark_col: str = "spx_return",
) -> dict | None:
    """
    Compute annual-return risk diagnostics for a strategy return series.
    Returns None if not enough observations.
    """
    required_cols = {"year", strategy_col, benchmark_col}
    if not required_cols.issubset(set(cmp_df.columns)):
        return None

    work = cmp_df[["year", strategy_col, benchmark_col]].dropna().copy()
    if len(work) < 3:
        return None
    work = work.sort_values("year").reset_index(drop=True)

    strat = work[strategy_col].astype(float) / 100.0
    bench = work[benchmark_col].astype(float) / 100.0
    alpha = strat - bench

    # Geometric growth and drawdown on annual series.
    equity = (1.0 + strat).cumprod()
    years = len(strat)
    if years <= 0 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return None
    cagr = (equity.iloc[-1] ** (1.0 / years)) - 1.0

    peak = equity.cummax()
    drawdown = (equity / peak) - 1.0
    max_drawdown = float(drawdown.min())

    vol = float(strat.std(ddof=1))
    rf = float(getattr(config, "RISK_FREE_RATE", 0.04))
    sharpe = ((float(strat.mean()) - rf) / vol) if vol > 0 else np.nan

    alpha_std = float(alpha.std(ddof=1))
    info_ratio = (float(alpha.mean()) / alpha_std) if alpha_std > 0 else np.nan

    bench_var = float(bench.var(ddof=1))
    if bench_var > 0:
        beta = float(np.cov(strat, bench, ddof=1)[0, 1] / bench_var)
    else:
        beta = np.nan

    return {
        "n_years": years,
        "avg_return": float(strat.mean() * 100),
        "cagr": float(cagr * 100),
        "vol": float(vol * 100),
        "sharpe": float(sharpe) if pd.notna(sharpe) else None,
        "max_drawdown": float(max_drawdown * 100),
        "info_ratio": float(info_ratio) if pd.notna(info_ratio) else None,
        "beta": float(beta) if pd.notna(beta) else None,
    }


def _render_benchmark_section(db_path: str, cache_buster: int, key_prefix: str = "") -> None:
    """
    Renders the Strategy vs. S&P 500 benchmark chart for:
    - All mapped companies (>= $1M Q4)
    - Production model portfolio (Top-N primary/fallback)

    Parameters
    ----------
    db_path      : path to the SQLite database
    cache_buster : st.session_state["cache_buster"] for cache invalidation
    key_prefix   : unique prefix for widget keys (prevents conflicts across tabs)
    """
    st.caption(
        "Equal-weight 1-year forward return from lag-adjusted Q4 signal snapshots. "
        "Year shown is the hold year (signal comes from prior-year filings). "
        "Compares the broad mapped universe (>= $1M Q4) against the production "
        "Top-N model and S&P 500."
    )
    lag_days = int(getattr(config, "LDA_FILING_LAG_DAYS", 20) or 0)
    st.caption(f"Signal lag assumption: {lag_days} day(s) after quarter-end.")
    cost_bps = float(getattr(config, "BACKTEST_REBALANCE_COST_BPS", 0.0) or 0.0)
    if cost_bps > 0:
        st.caption(
            f"Strategy returns are net of turnover-scaled costs ({cost_bps:.1f} bps per 100% turnover)."
        )

    prod_primary_factor = str(
        getattr(config, "PRODUCTION_PRIMARY_FACTOR", "hist_spend_z")
    )
    prod_fallback_factor = str(
        getattr(config, "PRODUCTION_FALLBACK_FACTOR", "accel_score")
    )
    prod_top_n = int(getattr(config, "PRODUCTION_TOP_N", 10))
    prod_min_usable = int(getattr(config, "PRODUCTION_MIN_USABLE_NAMES", 5))
    prod_min_spend = float(getattr(config, "MIN_LOBBYING_SPEND_DEFAULT", 1.0) or 1.0)

    bench_all = get_benchmark_comparison(db_path, refresh_token=cache_buster)
    prod_bt_df, _ = get_production_strategy_backtest(
        db_path,
        top_n=prod_top_n,
        primary_factor=prod_primary_factor,
        fallback_factor=prod_fallback_factor,
        refresh_token=cache_buster,
        min_spend_m=prod_min_spend,
        min_usable_names=prod_min_usable,
    )

    if bench_all.empty and prod_bt_df.empty:
        st.info(
            "No benchmark data yet. Populate stock performance for at least "
            "2 years (Performance tab -> Populate Stock Performance)."
        )
        return

    # Surface any S&P fetch warning from either dataset.
    _spx_err = None
    for _src in [bench_all, prod_bt_df]:
        if (
            not _src.empty
            and "_spx_error" in _src.columns
            and _src["_spx_error"].notna().any()
        ):
            _spx_err = _src["_spx_error"].dropna().iloc[0]
            break
    if _spx_err:
        st.warning(f"S&P 500 data issue: {_spx_err}")

    # Build merged comparison DataFrame (one row per hold year).
    cmp = pd.DataFrame()
    if not bench_all.empty:
        cmp = bench_all[
            [
                "signal_year",
                "year",
                "strategy_return",
                "n_companies",
                "spx_return",
                "turnover_pct",
                "cost_applied_pct",
                "return_coverage_pct",
                "entity_coverage_pct",
                "spend_coverage_pct",
                "mapped_companies",
            ]
        ].copy()
        cmp = cmp.rename(
            columns={
                "strategy_return": "all_ret",
                "n_companies": "all_n",
                "turnover_pct": "all_turnover_pct",
                "cost_applied_pct": "all_cost_pct",
                "return_coverage_pct": "all_return_cov_pct",
                "entity_coverage_pct": "all_entity_cov_pct",
                "spend_coverage_pct": "all_spend_cov_pct",
                "mapped_companies": "all_mapped_n",
            }
        )

    if not prod_bt_df.empty:
        prod_cmp = prod_bt_df[
            [
                "signal_year",
                "year",
                "top_return_1y",
                "n_selected",
                "return_coverage_pct",
                "model_used",
                "fallback_used",
                "spx_return",
            ]
        ].copy()
        prod_cmp = prod_cmp.rename(
            columns={
                "signal_year": "prod_signal_year",
                "top_return_1y": "prod_ret",
                "n_selected": "prod_n",
                "return_coverage_pct": "prod_return_cov_pct",
                "model_used": "prod_model_used",
                "fallback_used": "prod_fallback_used",
                "spx_return": "prod_spx_return",
            }
        )
        if cmp.empty:
            cmp = prod_cmp.copy()
            cmp["signal_year"] = cmp["prod_signal_year"]
            cmp["spx_return"] = cmp["prod_spx_return"]
        else:
            cmp = cmp.merge(prod_cmp, on="year", how="outer")
            if "prod_signal_year" in cmp.columns:
                cmp["signal_year"] = cmp["signal_year"].fillna(cmp["prod_signal_year"])
            if "prod_spx_return" in cmp.columns:
                cmp["spx_return"] = cmp["spx_return"].fillna(cmp["prod_spx_return"])

    for col in [
        "signal_year",
        "all_ret",
        "all_n",
        "all_turnover_pct",
        "all_cost_pct",
        "all_return_cov_pct",
        "all_entity_cov_pct",
        "all_spend_cov_pct",
        "all_mapped_n",
        "prod_ret",
        "prod_n",
        "prod_return_cov_pct",
        "prod_model_used",
        "prod_fallback_used",
    ]:
        if col not in cmp.columns:
            cmp[col] = None

    if "signal_year" in cmp.columns:
        cmp["signal_year"] = cmp["signal_year"].fillna(cmp["year"] - 1)
    else:
        cmp["signal_year"] = cmp["year"] - 1

    cmp["year"] = cmp["year"].astype(int)
    cmp = cmp.sort_values("year").reset_index(drop=True)

    years = cmp["year"].tolist()

    # KPI summary row
    valid = cmp[cmp["spx_return"].notna()].copy()
    spx_avg = valid["spx_return"].mean() if not valid.empty else None

    def _avg_alpha(col):
        if col not in valid.columns or valid[col].isna().all():
            return None
        return round((valid[col] - valid["spx_return"]).mean(), 1)

    def _beat_rate(col):
        if col not in valid.columns or valid[col].isna().all():
            return None
        return round((valid[col] > valid["spx_return"]).mean() * 100, 0)

    kc1, kc2, kc3, kc4, kc5 = st.columns(5)
    kc1.metric("Years Tracked", str(len(cmp)))
    kc2.metric("Avg S&P 500", f"{spx_avg:+.1f}%" if spx_avg is not None else "N/A")

    def _kpi_label(col, label):
        a = _avg_alpha(col)
        b = _beat_rate(col)
        if a is None:
            return "N/A"
        return f"α {a:+.1f}% / {b:.0f}% wins"

    kc3.metric("All companies alpha", _kpi_label("all_ret", "All"))
    kc4.metric(
        f"Production Top {prod_top_n} alpha",
        _kpi_label("prod_ret", f"Top {prod_top_n}"),
    )
    prod_win = _beat_rate("prod_ret")
    kc5.metric(
        f"Production Top {prod_top_n} win rate",
        f"{prod_win:.0f}%" if prod_win is not None else "N/A",
    )
    if "all_turnover_pct" in valid.columns and valid["all_turnover_pct"].notna().any():
        st.caption(
            f"Avg annual turnover (All): {valid['all_turnover_pct'].mean():.1f}% · "
            f"Avg return-data coverage: {valid['all_return_cov_pct'].mean():.1f}%"
        )
    if "prod_return_cov_pct" in valid.columns and valid["prod_return_cov_pct"].notna().any():
        st.caption(
            f"Avg return-data coverage (Production Top {prod_top_n}): "
            f"{valid['prod_return_cov_pct'].mean():.1f}%"
        )

    # Grouped bar chart
    palette = {
        "all": "#60a5fa",   # blue
        "prod": "#34d399",  # green
        "spx": "#f97316",   # orange
    }

    fig = go.Figure()

    def _bar(x, y, name, color):
        valid_mask = [v is not None and not (isinstance(v, float) and pd.isna(v)) for v in y]
        x_v = [x[i] for i in range(len(x)) if valid_mask[i]]
        y_v = [y[i] for i in range(len(y)) if valid_mask[i]]
        if not x_v:
            return
        fig.add_trace(go.Bar(
            x=x_v, y=y_v, name=name,
            marker_color=color,
            text=[f"{v:+.1f}%" for v in y_v],
            textposition="outside",
        ))

    _bar(years, cmp["all_ret"].tolist(), "All (>= $1M Q4)", palette["all"])
    _bar(
        years,
        cmp["prod_ret"].tolist(),
        f"Production Top {prod_top_n}",
        palette["prod"],
    )
    spx_vals = cmp["spx_return"].tolist()
    _bar(years, spx_vals, "S&P 500", palette["spx"])

    fig.add_hline(y=0, line_color="#6b7280", line_width=1)
    fig.update_layout(
        barmode="group",
        xaxis=dict(title="Hold Year", dtick=1, gridcolor="#2d3748", tickvals=years),
        yaxis=dict(title="1-Year Return (%)", gridcolor="#2d3748"),
        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
        font=dict(color="#ffffff", size=12),
        legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568", borderwidth=1,
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=420,
        margin=dict(l=0, r=40, t=50, b=0),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart")

    # Comparison table with alpha rows
    def _fmt(v):
        return f"{v:+.1f}%" if pd.notna(v) and v is not None else "N/A"

    prod_col_label = f"Production Top {prod_top_n} (N)"
    table_rows = []
    for _, r in cmp.iterrows():
        row = {
            "Signal Year": int(r["signal_year"]) if pd.notna(r.get("signal_year")) else "N/A",
            "Hold Year": int(r["year"]),
        }
        row["S&P 500"] = _fmt(r["spx_return"])
        row["All (N)"] = (
            f"{_fmt(r['all_ret'])} ({int(r['all_n']) if pd.notna(r.get('all_n')) else '?'})"
            if pd.notna(r.get("all_ret"))
            else "N/A"
        )
        row[prod_col_label] = (
            f"{_fmt(r['prod_ret'])} ({int(r['prod_n']) if pd.notna(r.get('prod_n')) else '?'})"
            if pd.notna(r.get("prod_ret"))
            else "N/A"
        )
        row["Prod Model"] = (
            str(r["prod_model_used"])
            + (" (fallback)" if bool(r.get("prod_fallback_used")) else "")
            if pd.notna(r.get("prod_model_used"))
            else "N/A"
        )
        table_rows.append(row)

    # Avg alpha summary row
    summary = {"Signal Year": "—", "Hold Year": "Avg Alpha"}
    summary["S&P 500"] = "—"
    for col, label in [("all_ret", "All (N)"), ("prod_ret", prod_col_label)]:
        a = _avg_alpha(col)
        summary[label] = f"{a:+.1f}% vs SPX" if a is not None else "N/A"
    summary["Prod Model"] = "—"
    table_rows.append(summary)

    st.dataframe(
        pd.DataFrame(table_rows),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Execution & Coverage Diagnostics", expanded=False):
        diag_cols = [
            "signal_year",
            "year",
            "all_n",
            "all_mapped_n",
            "all_return_cov_pct",
            "all_entity_cov_pct",
            "all_spend_cov_pct",
            "all_turnover_pct",
            "all_cost_pct",
            "prod_n",
            "prod_model_used",
            "prod_fallback_used",
            "prod_return_cov_pct",
        ]
        diag_cols = [c for c in diag_cols if c in cmp.columns]
        diag = cmp[diag_cols].copy()
        diag = diag.rename(
            columns={
                "signal_year": "Signal Year",
                "year": "Hold Year",
                "all_n": "All N (with returns)",
                "all_mapped_n": "All N (mapped)",
                "all_return_cov_pct": "All Return Coverage",
                "all_entity_cov_pct": "All Entity Coverage",
                "all_spend_cov_pct": "All Spend Coverage",
                "all_turnover_pct": "All Turnover",
                "all_cost_pct": "All Cost Drag",
                "prod_n": "Production N (with returns)",
                "prod_model_used": "Production Model",
                "prod_fallback_used": "Production Fallback Used",
                "prod_return_cov_pct": "Production Return Coverage",
            }
        )
        pct_cols = [
            c
            for c in diag.columns
            if any(k in c for k in ["Coverage", "Turnover", "Cost Drag"])
        ]
        for c in pct_cols:
            diag[c] = diag[c].apply(
                lambda v: f"{v:.2f}%" if pd.notna(v) else "N/A"
            )
        st.dataframe(diag, use_container_width=True, hide_index=True)

    with st.expander("Risk Metrics", expanded=False):
        st.caption(
            "Annual return risk profile using geometric compounding. "
            "Metrics shown: CAGR, volatility, Sharpe, max drawdown, information ratio, beta."
        )
        risk_specs = [
            ("All (≥$1M Q4)", "all_ret"),
            (f"Production Top {prod_top_n}", "prod_ret"),
        ]
        risk_rows = []
        for label, col in risk_specs:
            stats = _compute_return_risk_stats(cmp, col, benchmark_col="spx_return")
            if stats is None:
                continue
            risk_rows.append({
                "Portfolio": label,
                "Years": stats["n_years"],
                "Avg Return": f"{stats['avg_return']:+.1f}%",
                "CAGR": f"{stats['cagr']:+.1f}%",
                "Volatility": f"{stats['vol']:.1f}%",
                "Sharpe": (
                    f"{stats['sharpe']:.2f}" if stats["sharpe"] is not None else "N/A"
                ),
                "Max Drawdown": f"{stats['max_drawdown']:.1f}%",
                "Info Ratio": (
                    f"{stats['info_ratio']:.2f}" if stats["info_ratio"] is not None else "N/A"
                ),
                "Beta vs S&P": (
                    f"{stats['beta']:.2f}" if stats["beta"] is not None else "N/A"
                ),
            })

        if not risk_rows:
            st.info("Need at least 3 annual observations per portfolio to compute risk stats.")
        else:
            st.dataframe(pd.DataFrame(risk_rows), use_container_width=True, hide_index=True)

    st.markdown("---")
    with st.expander("Overfitting Diagnostics", expanded=False):
        st.caption(
            "Chronological train/test split plus bootstrap confidence checks on annual alpha. "
            "Use this to validate whether a strategy remains stable out-of-sample."
        )

        diag_specs = [
            ("All (≥$1M Q4)", "all_ret"),
            (f"Production Top {prod_top_n}", "prod_ret"),
        ]
        diag_rows = []
        raw_pvalues = []
        for label, col in diag_specs:
            diag = _compute_overfit_diagnostics(cmp, col, benchmark_col="spx_return")
            if diag is None:
                continue
            raw_pvalues.append((label, float(diag["p_binom"])))
            diag_rows.append({
                "Portfolio": label,
                "Years": diag["n_years"],
                "Train/Test": diag["split"],
                "Avg Alpha": f"{diag['avg_alpha']:+.1f}%",
                "Train Alpha": f"{diag['train_alpha']:+.1f}%",
                "Test Alpha": f"{diag['test_alpha']:+.1f}%",
                "Alpha Drift": f"{diag['alpha_drift']:+.1f}%",
                "Hit Rate": f"{diag['hit_rate']:.0f}%",
                "Test Hit Rate": f"{diag['test_hit_rate']:.0f}%",
                "95% CI (Mean Alpha)": f"[{diag['ci_low']:+.1f}%, {diag['ci_high']:+.1f}%]",
                "Binom p-value": f"{diag['p_binom']:.3f}",
                "Stability": diag["verdict"],
            })

        if not diag_rows:
            st.info("Need at least 4 annual observations per portfolio to run diagnostics.")
        else:
            # Multiple-testing correction across the tested portfolio variants.
            n_tests = max(1, len(raw_pvalues))
            adj_map = {
                label: min(1.0, p * n_tests) for label, p in raw_pvalues
            }
            for row in diag_rows:
                p_adj = adj_map.get(row["Portfolio"])
                row["Adj p-value"] = f"{p_adj:.3f}" if p_adj is not None else "N/A"
                row["Sig @10%"] = (
                    "Yes" if p_adj is not None and p_adj < 0.10 else "No"
                )

            diag_df = pd.DataFrame(diag_rows)
            st.dataframe(diag_df, use_container_width=True, hide_index=True)

            fragile = diag_df[diag_df["Stability"] == "Fragile"]["Portfolio"].tolist()
            if fragile:
                st.warning(
                    "Potential overfitting risk: "
                    + ", ".join(fragile)
                    + " show weaker out-of-sample alpha than in-sample."
                )
            if len(diag_df) > 0 and (diag_df["Sig @10%"] == "No").all():
                st.caption(
                    "None of the tested portfolio variants pass a 10% significance threshold "
                    "after multiple-testing adjustment."
                )


def create_lobbying_treemap(data: pd.DataFrame, year: int):
    """
    Treemap of lobbying spend, grouped by sector → company/ticker.
    Colour intensity reflects spend magnitude (blue → green scale).
    """
    df = data[data["sector"].notna() & (data["total_lobbying_spend"] > 0)].copy()
    df["label"] = df.apply(
        lambda r: r["ticker"]
        if pd.notna(r["ticker"]) and str(r["ticker"]).strip()
        else r["company_name"][:22],
        axis=1,
    )
    df["spend_m"] = (df["total_lobbying_spend"] / 1_000_000).round(2)

    fig = px.treemap(
        df,
        path=["sector", "label"],
        values="total_lobbying_spend",
        color="spend_m",
        color_continuous_scale=[[0, "#1e3a5f"], [0.5, "#60a5fa"], [1.0, "#22c55e"]],
        custom_data=["spend_m"],
    )
    fig.update_traces(
        texttemplate="%{label}<br>$%{customdata[0]:.1f}M",
        hovertemplate="<b>%{label}</b><br>Spend: $%{customdata[0]:.1f}M<extra></extra>",
    )
    fig.update_layout(
        title=f"Lobbying Spend by Sector & Company — {year}",
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font=dict(color="#ffffff", size=12),
        height=520,
        margin=dict(l=0, r=0, t=45, b=0),
        coloraxis_showscale=False,
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
#  COMPANY RESEARCH — DATA FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

_PERIOD_TO_QUARTER = {
    "first_quarter": "Q1",
    "second_quarter": "Q2",
    "third_quarter": "Q3",
    "fourth_quarter": "Q4",
}


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

    spx_dict, _ = _fetch_spx_returns_for_signal_years(
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


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL 4 — ACCELERATION / SPIKE FACTOR LAB
# ─────────────────────────────────────────────────────────────────────────────


def _safe_pct_change(curr, prev):
    if pd.isna(curr) or pd.isna(prev) or float(prev) <= 0:
        return None
    return float((float(curr) - float(prev)) / float(prev) * 100.0)


def _to_period_idx(year_val, quarter_val):
    q_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    q_num = q_map.get(str(quarter_val).strip().upper())
    if q_num is None:
        return None
    return int(year_val) * 4 + q_num


@st.cache_data
def get_acceleration_features(
    db_path,
    year: int,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    ticker_only: bool = True,
):
    """
    Build per-entity acceleration features for a given signal year.

    Features implemented:
      - YoY change in annual spend
      - QoQ change in quarterly spend
      - Spend z-score vs entity historical average
      - Sector-normalized spike (z-score of YoY within sector)
      - YoY % change in spend/market-cap ratio
    """
    import sqlite3

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
                SUM(total_lobbying_spend) AS q_spend
            FROM company_lobbying
            WHERE year BETWEEN :min_hist_year AND :year
              AND total_lobbying_spend > 0
            GROUP BY entity_key, year, quarter
            """,
            conn,
            params={"min_hist_year": min_hist_year, "year": int(year)},
        )
    finally:
        conn.close()

    if annual_df.empty:
        return pd.DataFrame()

    current = annual_df[annual_df["year"] == int(year)].copy()
    if current.empty:
        return pd.DataFrame()

    if ticker_only:
        current = current[current["ticker"].notna() & (current["ticker"].astype(str).str.strip() != "")].copy()
    if current.empty:
        return pd.DataFrame()

    current = current[current["annual_spend"] >= min_spend].copy()
    if current.empty:
        return pd.DataFrame()

    annual_by_entity = {
        ent: grp.sort_values("year").copy()
        for ent, grp in annual_df.groupby("entity_key", dropna=False)
    }
    quarterly_df = quarterly_df.copy()
    quarterly_df["period_idx"] = quarterly_df.apply(
        lambda r: _to_period_idx(r["year"], r["quarter"]), axis=1
    )
    quarterly_df = quarterly_df[quarterly_df["period_idx"].notna()].copy()
    quarterly_by_entity = {
        ent: grp.sort_values("period_idx").copy()
        for ent, grp in quarterly_df.groupby("entity_key", dropna=False)
    }

    rows = []
    for _, row in current.iterrows():
        entity_key = row["entity_key"]
        hist = annual_by_entity.get(entity_key)
        if hist is None or hist.empty:
            continue
        hist = hist.sort_values("year")
        curr_spend = float(row["annual_spend"] or 0.0)
        curr_ratio = (
            (curr_spend / float(row["market_cap"]))
            if pd.notna(row["market_cap"]) and float(row["market_cap"]) > 0
            else None
        )

        prev_row = hist[hist["year"] == int(year) - 1]
        prev_spend = float(prev_row.iloc[0]["annual_spend"]) if not prev_row.empty else None
        prev_ratio = (
            (prev_spend / float(prev_row.iloc[0]["market_cap"]))
            if (
                not prev_row.empty
                and pd.notna(prev_row.iloc[0]["market_cap"])
                and float(prev_row.iloc[0]["market_cap"]) > 0
                and prev_spend is not None
            )
            else None
        )

        yoy_pct = _safe_pct_change(curr_spend, prev_spend)
        mcap_ratio_yoy_pct = _safe_pct_change(curr_ratio, prev_ratio)

        hist_prior = hist[hist["year"] < int(year)]["annual_spend"].dropna().astype(float)
        hist_mean = float(hist_prior.mean()) if not hist_prior.empty else None
        hist_std = float(hist_prior.std(ddof=0)) if len(hist_prior) >= 2 else None
        z_hist = (
            float((curr_spend - hist_mean) / hist_std)
            if hist_mean is not None and hist_std and hist_std > 0
            else None
        )

        q_hist = quarterly_by_entity.get(entity_key)
        qoq_pct = None
        if q_hist is not None and not q_hist.empty:
            q_hist = q_hist[q_hist["period_idx"] <= (int(year) * 4 + 4)].sort_values("period_idx")
            if len(q_hist) >= 2:
                latest_q = float(q_hist.iloc[-1]["q_spend"])
                prior_q = float(q_hist.iloc[-2]["q_spend"])
                qoq_pct = _safe_pct_change(latest_q, prior_q)

        rows.append(
            {
                "entity_key": entity_key,
                "company_name": row.get("company_name"),
                "ticker": row.get("ticker"),
                "sector": row.get("sector"),
                "signal_year": int(year),
                "annual_spend": curr_spend,
                "market_cap": row.get("market_cap"),
                "spend_to_mcap_ratio": curr_ratio,
                "prev_spend": prev_spend,
                "yoy_pct": yoy_pct,
                "qoq_pct": qoq_pct,
                "hist_spend_z": z_hist,
                "mcap_ratio_yoy_pct": mcap_ratio_yoy_pct,
            }
        )

    if not rows:
        return pd.DataFrame()

    feat_df = pd.DataFrame(rows)

    # Industry-normalized spike: z-score of YoY within sector/year.
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


@st.cache_data
def get_acceleration_event_backtest(
    db_path,
    top_n: int = 20,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
):
    """
    Annual backtest for acceleration factor by event windows (1m/3m/6m/1y).
    Compares top-N acceleration names vs investable ticker universe each year.
    """
    import sqlite3

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

    if not years:
        return pd.DataFrame(), pd.DataFrame()

    rows = []
    detail_rows = []
    for signal_year in years:
        feat_df = get_acceleration_features(
            db_path,
            year=int(signal_year),
            refresh_token=refresh_token,
            min_spend_m=min_spend_m,
            ticker_only=True,
        )
        if feat_df.empty:
            continue

        ret_df = get_signal_returns(db_path, int(signal_year), refresh_token=refresh_token)
        if ret_df.empty:
            continue

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
            continue

        merged = merged.sort_values(["accel_score", "annual_spend"], ascending=[False, False])
        top_df = merged.head(int(top_n)).copy()
        if top_df.empty:
            continue

        hold_year = int(signal_year) + 1
        summary = {
            "signal_year": int(signal_year),
            "year": hold_year,
            "n_universe": int(len(merged)),
            "n_top": int(len(top_df)),
        }
        for col in ["return_1m", "return_3m", "return_6m", "return_1y"]:
            summary[f"top_{col}"] = (
                round(float(top_df[col].dropna().mean()), 2)
                if top_df[col].notna().any()
                else None
            )
            summary[f"universe_{col}"] = (
                round(float(merged[col].dropna().mean()), 2)
                if merged[col].notna().any()
                else None
            )
            if (
                summary[f"top_{col}"] is not None
                and summary[f"universe_{col}"] is not None
            ):
                summary[f"alpha_{col}"] = round(
                    summary[f"top_{col}"] - summary[f"universe_{col}"], 2
                )
            else:
                summary[f"alpha_{col}"] = None

        rows.append(summary)

        keep_cols = [
            "signal_year",
            "ticker",
            "company_name",
            "sector",
            "annual_spend",
            "accel_score",
            "yoy_pct",
            "qoq_pct",
            "hist_spend_z",
            "sector_spike_z",
            "mcap_ratio_yoy_pct",
            "return_1m",
            "return_3m",
            "return_6m",
            "return_1y",
        ]
        top_detail = top_df.copy()
        top_detail["signal_year"] = int(signal_year)
        detail_rows.extend(top_detail[keep_cols].to_dict("records"))

    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    bt_df = pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
    detail_df = (
        pd.DataFrame(detail_rows).sort_values(["signal_year", "accel_score"], ascending=[True, False])
        if detail_rows
        else pd.DataFrame()
    )

    if not bt_df.empty:
        spx_map, spx_err = _fetch_spx_returns_for_signal_years(
            bt_df["signal_year"].astype(int).tolist(),
            prefer_forward_window=True,
        )
        bt_df["spx_return"] = bt_df["year"].map(spx_map)
        bt_df["alpha_vs_spx_1y"] = bt_df.apply(
            lambda r: round(r["top_return_1y"] - r["spx_return"], 2)
            if pd.notna(r.get("top_return_1y")) and pd.notna(r.get("spx_return"))
            else None,
            axis=1,
        )
        bt_df["_spx_error"] = spx_err

    return bt_df, detail_df


def _get_complete_signal_years(db_path: str) -> list[int]:
    import sqlite3

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


def _build_acceleration_year_frame(
    db_path: str,
    signal_year: int,
    refresh_token: int,
    min_spend_m: float,
) -> pd.DataFrame:
    feat_df = get_acceleration_features(
        db_path,
        year=int(signal_year),
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
        ticker_only=True,
    )
    if feat_df.empty:
        return pd.DataFrame()

    ret_df = get_signal_returns(db_path, int(signal_year), refresh_token=refresh_token)
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


def _rank_acceleration_factor(df: pd.DataFrame, factor_col: str, top_n: int) -> pd.DataFrame:
    """Rank a frame by one factor descending and return top-N."""
    if df is None or df.empty or factor_col not in df.columns:
        return pd.DataFrame()
    ranked = df[df[factor_col].notna()].copy()
    if ranked.empty:
        return pd.DataFrame()
    return ranked.sort_values([factor_col, "annual_spend"], ascending=[False, False]).head(int(top_n))


def _select_production_portfolio(
    df: pd.DataFrame,
    top_n: int,
    primary_factor: str,
    fallback_factor: str,
    min_usable_names: int = 5,
    require_return_col: str | None = None,
) -> tuple[pd.DataFrame, str, bool, str | None]:
    """
    Select portfolio by primary factor, falling back to secondary factor if
    primary has insufficient usable names for this year.
    """
    if df is None or df.empty:
        return pd.DataFrame(), primary_factor, False, "No data for this year."

    required = max(1, min(int(top_n), int(min_usable_names)))

    def _usable_count(sel: pd.DataFrame) -> int:
        if sel.empty:
            return 0
        if require_return_col and require_return_col in sel.columns:
            return int(sel[require_return_col].notna().sum())
        return int(len(sel))

    primary_sel = _rank_acceleration_factor(df, primary_factor, top_n)
    primary_usable = _usable_count(primary_sel)
    if primary_usable >= required:
        return primary_sel, primary_factor, False, None

    fallback_sel = _rank_acceleration_factor(df, fallback_factor, top_n)
    fallback_usable = _usable_count(fallback_sel)

    # Prefer fallback if it reaches required coverage when primary does not.
    if fallback_usable >= required:
        return (
            fallback_sel,
            fallback_factor,
            True,
            (
                f"Primary factor `{primary_factor}` had {primary_usable}/{required} usable names; "
                f"used fallback `{fallback_factor}` with {fallback_usable}/{required}."
            ),
        )

    # If neither reaches threshold, keep the richer candidate to avoid empty results.
    if fallback_usable > primary_usable:
        return (
            fallback_sel,
            fallback_factor,
            True,
            (
                f"Both models below required coverage ({required}); "
                f"used fallback `{fallback_factor}` ({fallback_usable} usable) "
                f"over primary `{primary_factor}` ({primary_usable} usable)."
            ),
        )

    if not primary_sel.empty:
        return (
            primary_sel,
            primary_factor,
            False,
            (
                f"Primary factor `{primary_factor}` below required coverage "
                f"({primary_usable}/{required}), fallback not better."
            ),
        )

    if not fallback_sel.empty:
        return (
            fallback_sel,
            fallback_factor,
            True,
            (
                f"Primary factor `{primary_factor}` unavailable; "
                f"used fallback `{fallback_factor}`."
            ),
        )

    return pd.DataFrame(), primary_factor, False, "No eligible ranked names."


@st.cache_data
def get_production_strategy_holdings(
    db_path,
    hold_year: int,
    top_n: int = 10,
    primary_factor: str = "hist_spend_z",
    fallback_factor: str = "accel_score",
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    min_usable_names: int = 5,
):
    """
    Build live holdings for a given hold year using primary/fallback model logic.
    Returns (holdings_df, meta_dict).
    """
    signal_year = int(hold_year) - 1

    feat_df = get_acceleration_features(
        db_path,
        year=signal_year,
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
        ticker_only=True,
    )
    if feat_df.empty:
        return pd.DataFrame(), {
            "hold_year": int(hold_year),
            "signal_year": signal_year,
            "model_used": None,
            "fallback_used": False,
            "reason": "No feature frame available for this signal year.",
        }

    ret_df = get_signal_returns(db_path, signal_year, refresh_token=refresh_token)
    if ret_df.empty:
        frame = feat_df.copy()
        frame["return_1m"] = None
        frame["return_3m"] = None
        frame["return_6m"] = None
        frame["return_1y"] = None
        frame["signal_date"] = None
    else:
        frame = feat_df.merge(
            ret_df[
                [
                    "ticker",
                    "signal_date",
                    "return_1m",
                    "return_3m",
                    "return_6m",
                    "return_1y",
                    "total_spend",
                ]
            ],
            on="ticker",
            how="left",
        )

    selected, model_used, fallback_used, reason = _select_production_portfolio(
        frame,
        top_n=int(top_n),
        primary_factor=str(primary_factor),
        fallback_factor=str(fallback_factor),
        min_usable_names=int(min_usable_names),
        require_return_col=None,
    )
    if selected.empty:
        return pd.DataFrame(), {
            "hold_year": int(hold_year),
            "signal_year": signal_year,
            "model_used": model_used,
            "fallback_used": bool(fallback_used),
            "reason": reason or "No selected names.",
        }

    selected = selected.copy()
    selected["hold_year"] = int(hold_year)
    selected["signal_year"] = signal_year
    selected["model_used"] = model_used

    meta = {
        "hold_year": int(hold_year),
        "signal_year": signal_year,
        "model_used": model_used,
        "fallback_used": bool(fallback_used),
        "reason": reason,
        "selected_n": int(len(selected)),
        "selected_with_1y": int(selected["return_1y"].notna().sum())
        if "return_1y" in selected.columns
        else 0,
    }
    return selected, meta


@st.cache_data
def get_production_strategy_backtest(
    db_path,
    top_n: int = 10,
    primary_factor: str = "hist_spend_z",
    fallback_factor: str = "accel_score",
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    min_usable_names: int = 5,
):
    """
    Annual production-model backtest with primary/fallback factor routing.
    """
    years = _get_complete_signal_years(db_path)
    if not years:
        return pd.DataFrame(), pd.DataFrame()

    rows = []
    detail_rows = []
    for signal_year in years:
        frame = _build_acceleration_year_frame(
            db_path,
            signal_year=int(signal_year),
            refresh_token=refresh_token,
            min_spend_m=min_spend_m,
        )
        if frame.empty:
            continue

        selected, model_used, fallback_used, reason = _select_production_portfolio(
            frame,
            top_n=int(top_n),
            primary_factor=str(primary_factor),
            fallback_factor=str(fallback_factor),
            min_usable_names=int(min_usable_names),
            require_return_col="return_1y",
        )
        if selected.empty:
            continue

        hold_year = int(signal_year) + 1
        top_row = {
            "signal_year": int(signal_year),
            "year": hold_year,
            "model_used": model_used,
            "fallback_used": bool(fallback_used),
            "fallback_reason": reason,
            "n_universe": int(len(frame)),
            "n_selected": int(len(selected)),
        }

        for col in ["return_1m", "return_3m", "return_6m", "return_1y"]:
            sel_ret = selected[col].dropna()
            uni_ret = frame[col].dropna()
            top_row[f"top_{col}"] = round(float(sel_ret.mean()), 2) if not sel_ret.empty else None
            top_row[f"universe_{col}"] = round(float(uni_ret.mean()), 2) if not uni_ret.empty else None
            if top_row[f"top_{col}"] is not None and top_row[f"universe_{col}"] is not None:
                top_row[f"alpha_{col}"] = round(
                    top_row[f"top_{col}"] - top_row[f"universe_{col}"], 2
                )
            else:
                top_row[f"alpha_{col}"] = None
        top_row["return_coverage_pct"] = round(
            100.0 * float(selected["return_1y"].notna().mean()), 1
        )
        rows.append(top_row)

        keep_cols = [
            "signal_year",
            "year",
            "model_used",
            "fallback_used",
            "ticker",
            "company_name",
            "sector",
            "annual_spend",
            "hist_spend_z",
            "accel_score",
            "yoy_pct",
            "qoq_pct",
            "mcap_ratio_yoy_pct",
            "return_1m",
            "return_3m",
            "return_6m",
            "return_1y",
        ]
        tmp = selected.copy()
        tmp["signal_year"] = int(signal_year)
        tmp["year"] = hold_year
        tmp["model_used"] = model_used
        tmp["fallback_used"] = bool(fallback_used)
        detail_rows.extend(tmp[[c for c in keep_cols if c in tmp.columns]].to_dict("records"))

    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    bt_df = pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
    det_df = (
        pd.DataFrame(detail_rows).sort_values(["signal_year", "annual_spend"], ascending=[True, False])
        if detail_rows
        else pd.DataFrame()
    )

    spx_map, spx_err = _fetch_spx_returns_for_signal_years(
        bt_df["signal_year"].astype(int).tolist(),
        prefer_forward_window=True,
    )
    bt_df["spx_return"] = bt_df["year"].map(spx_map)
    bt_df["alpha_vs_spx_1y"] = bt_df.apply(
        lambda r: round(r["top_return_1y"] - r["spx_return"], 2)
        if pd.notna(r.get("top_return_1y")) and pd.notna(r.get("spx_return"))
        else None,
        axis=1,
    )
    bt_df["_spx_error"] = spx_err
    return bt_df, det_df


@st.cache_data
def get_production_go_nogo_metrics(
    db_path,
    top_n: int = 10,
    primary_factor: str = "hist_spend_z",
    fallback_factor: str = "accel_score",
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    min_usable_names: int = 5,
    sector_k: int = 1,
):
    """
    Compute strict go/no-go diagnostics for production deployment.

    Gates:
      - Net alpha vs S&P 500
      - Outlier robustness (drop best name/year)
      - Sector-neutral robustness
      - Statistical confidence (binomial + bootstrap CI)
    """
    bt_df, det_df = get_production_strategy_backtest(
        db_path,
        top_n=top_n,
        primary_factor=primary_factor,
        fallback_factor=fallback_factor,
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
        min_usable_names=min_usable_names,
    )
    if bt_df.empty:
        return {"ready": False, "reason": "No production backtest rows."}, pd.DataFrame()
    spx_error = (
        bt_df["_spx_error"].dropna().iloc[0]
        if "_spx_error" in bt_df.columns and bt_df["_spx_error"].notna().any()
        else None
    )

    eval_df = bt_df.dropna(subset=["alpha_vs_spx_1y"]).copy()
    if eval_df.empty:
        return {"ready": False, "reason": "No rows with SPX-aligned alpha."}, pd.DataFrame()
    eval_df = eval_df.sort_values("year").reset_index(drop=True)

    # Turnover/cost-adjusted (net) alpha.
    cost_bps = float(getattr(config, "BACKTEST_REBALANCE_COST_BPS", 0.0) or 0.0)
    turnover_rows = []
    prev_constituents = []
    if not det_df.empty and {"year", "ticker"}.issubset(det_df.columns):
        dtmp = det_df.copy()
        dtmp["year"] = pd.to_numeric(dtmp["year"], errors="coerce")
        for year in eval_df["year"].astype(int).tolist():
            curr_constituents = _sanitize_ticker_list(
                dtmp.loc[dtmp["year"] == year, "ticker"].dropna().tolist()
            )
            turnover = _equal_weight_turnover_pct(prev_constituents, curr_constituents)
            cost_pct = _cost_pct_from_turnover(turnover, cost_bps)
            turnover_rows.append(
                {
                    "year": year,
                    "turnover_pct": turnover,
                    "cost_pct": cost_pct,
                }
            )
            prev_constituents = curr_constituents
    if turnover_rows:
        eval_df = eval_df.merge(pd.DataFrame(turnover_rows), on="year", how="left")
    if "cost_pct" not in eval_df.columns:
        eval_df["cost_pct"] = 0.0
    if "turnover_pct" not in eval_df.columns:
        eval_df["turnover_pct"] = np.nan

    eval_df["net_alpha_vs_spx_1y"] = eval_df["alpha_vs_spx_1y"] - eval_df["cost_pct"].fillna(0.0)

    # Outlier robustness: recompute alpha excluding the single best constituent each year.
    ex_best_rows = []
    if not det_df.empty and {"year", "return_1y"}.issubset(det_df.columns):
        d2 = det_df.dropna(subset=["year", "return_1y"]).copy()
        d2["year"] = pd.to_numeric(d2["year"], errors="coerce")
        d2 = d2.dropna(subset=["year"])
        d2["year"] = d2["year"].astype(int)
        for year in eval_df["year"].astype(int).tolist():
            vals = d2.loc[d2["year"] == year, "return_1y"].dropna().astype(float)
            spx_s = eval_df.loc[eval_df["year"] == year, "spx_return"].dropna()
            if len(vals) < 2 or spx_s.empty:
                continue
            ex_best_mean = float(vals.sort_values(ascending=False).iloc[1:].mean())
            ex_best_rows.append(
                {
                    "year": year,
                    "alpha_ex_best_1y": ex_best_mean - float(spx_s.iloc[0]),
                }
            )
    if ex_best_rows:
        eval_df = eval_df.merge(pd.DataFrame(ex_best_rows), on="year", how="left")
    else:
        eval_df["alpha_ex_best_1y"] = np.nan

    # Statistical confidence on raw alpha series.
    alpha_vals = eval_df["alpha_vs_spx_1y"].dropna().astype(float)
    if alpha_vals.empty:
        return {"ready": False, "reason": "Alpha series is empty."}, eval_df
    wins = int((alpha_vals > 0).sum())
    n_obs = int(len(alpha_vals))
    p_binom = 0.0
    for k in range(wins, n_obs + 1):
        p_binom += math.comb(n_obs, k) * (0.5 ** n_obs)

    rng = np.random.default_rng(42)
    boot = rng.choice(alpha_vals.to_numpy(), size=(4000, n_obs), replace=True).mean(axis=1)
    ci_low = float(np.percentile(boot, 2.5))
    ci_high = float(np.percentile(boot, 97.5))

    # Sector-neutral robustness (separate signal family sanity check).
    sn_df = get_sector_neutral_acceleration_backtest(
        db_path,
        top_k_per_sector=int(sector_k),
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
    )
    sn_alpha_vals = pd.Series(dtype=float)
    if not sn_df.empty:
        sn_eval = sn_df.dropna(subset=["signal_year", "year", "ls_return_1y"]).copy()
        if not sn_eval.empty:
            sn_eval["signal_year"] = sn_eval["signal_year"].astype(int)
            sn_map, _ = _fetch_spx_returns_for_signal_years(
                sn_eval["signal_year"].tolist(),
                prefer_forward_window=True,
            )
            sn_eval["spx_return"] = sn_eval["year"].map(sn_map)
            sn_eval["ls_alpha_vs_spx_1y"] = sn_eval["ls_return_1y"] - sn_eval["spx_return"]
            sn_alpha_vals = sn_eval["ls_alpha_vs_spx_1y"].dropna().astype(float)

    # Gate thresholds
    min_net_alpha = float(getattr(config, "GO_NOGO_MIN_NET_ALPHA_PCT", 2.0))
    min_win_rate = float(getattr(config, "GO_NOGO_MIN_WIN_RATE_PCT", 55.0))
    min_robust_alpha = float(getattr(config, "GO_NOGO_MIN_ROBUST_ALPHA_PCT", 0.0))
    min_sector_alpha = float(getattr(config, "GO_NOGO_MIN_SECTOR_NEUTRAL_ALPHA_PCT", 0.0))
    p_cutoff = float(getattr(config, "GO_NOGO_BINOM_P_CUTOFF", 0.10))
    require_ci_positive = bool(getattr(config, "GO_NOGO_REQUIRE_CI_POSITIVE", True))

    avg_net_alpha = float(eval_df["net_alpha_vs_spx_1y"].dropna().mean())
    net_win_rate = float((eval_df["net_alpha_vs_spx_1y"].dropna() > 0).mean() * 100.0)
    avg_robust_alpha = float(eval_df["alpha_ex_best_1y"].dropna().mean()) if eval_df["alpha_ex_best_1y"].notna().any() else np.nan
    robust_win_rate = float((eval_df["alpha_ex_best_1y"].dropna() > 0).mean() * 100.0) if eval_df["alpha_ex_best_1y"].notna().any() else np.nan
    avg_sector_alpha = float(sn_alpha_vals.mean()) if not sn_alpha_vals.empty else np.nan
    sector_win_rate = float((sn_alpha_vals > 0).mean() * 100.0) if not sn_alpha_vals.empty else np.nan

    pass_net = (avg_net_alpha >= min_net_alpha) and (net_win_rate >= min_win_rate)
    pass_robust = (
        pd.notna(avg_robust_alpha)
        and (avg_robust_alpha >= min_robust_alpha)
        and (pd.notna(robust_win_rate) and robust_win_rate >= 50.0)
    )
    pass_sector = (
        pd.notna(avg_sector_alpha)
        and (avg_sector_alpha >= min_sector_alpha)
        and (pd.notna(sector_win_rate) and sector_win_rate >= 50.0)
    )
    pass_significance = (p_binom <= p_cutoff) and (
        (ci_low > 0.0) if require_ci_positive else True
    )
    go_live = bool(pass_net and pass_robust and pass_sector and pass_significance)

    summary = {
        "ready": True,
        "go_live": go_live,
        "spx_error": spx_error,
        "n_years": n_obs,
        "avg_alpha_raw": float(alpha_vals.mean()),
        "avg_alpha_net": avg_net_alpha,
        "win_rate_raw": float((alpha_vals > 0).mean() * 100.0),
        "win_rate_net": net_win_rate,
        "avg_alpha_ex_best": avg_robust_alpha,
        "win_rate_ex_best": robust_win_rate,
        "avg_turnover_pct": float(eval_df["turnover_pct"].dropna().mean()) if eval_df["turnover_pct"].notna().any() else np.nan,
        "avg_cost_drag_pct": float(eval_df["cost_pct"].dropna().mean()) if eval_df["cost_pct"].notna().any() else np.nan,
        "avg_sector_neutral_alpha": avg_sector_alpha,
        "win_rate_sector_neutral": sector_win_rate,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_binom": float(p_binom),
        "pass_net": pass_net,
        "pass_robust": pass_robust,
        "pass_sector": pass_sector,
        "pass_significance": pass_significance,
        "threshold_net_alpha": min_net_alpha,
        "threshold_win_rate": min_win_rate,
        "threshold_robust_alpha": min_robust_alpha,
        "threshold_sector_alpha": min_sector_alpha,
        "threshold_p": p_cutoff,
    }
    return summary, eval_df


@st.cache_data
def get_acceleration_rolling_windows(
    db_path,
    top_n: int = 20,
    window_years: int = 3,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
):
    """
    Rolling-window stability test for acceleration alpha.
    Example windows for 3 years: 2019–2021, 2020–2022, ...
    """
    win = max(int(window_years), 2)
    bt_df, _ = get_acceleration_event_backtest(
        db_path,
        top_n=top_n,
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
    )
    if bt_df.empty:
        return pd.DataFrame()

    years = sorted(bt_df["signal_year"].dropna().astype(int).unique().tolist())
    if len(years) < win:
        return pd.DataFrame()

    rows = []
    for i in range(0, len(years) - win + 1):
        y0 = years[i]
        y1 = years[i + win - 1]
        sub = bt_df[(bt_df["signal_year"] >= y0) & (bt_df["signal_year"] <= y1)].copy()
        if sub.empty:
            continue

        rows.append(
            {
                "window_start": y0,
                "window_end": y1,
                "window_label": f"{y0}-{y1}",
                "n_years": int(len(sub)),
                "avg_alpha_3m": round(float(sub["alpha_return_3m"].dropna().mean()), 2)
                if sub["alpha_return_3m"].notna().any()
                else None,
                "avg_alpha_6m": round(float(sub["alpha_return_6m"].dropna().mean()), 2)
                if sub["alpha_return_6m"].notna().any()
                else None,
                "avg_alpha_1y": round(float(sub["alpha_return_1y"].dropna().mean()), 2)
                if sub["alpha_return_1y"].notna().any()
                else None,
                "win_rate_1y": round(
                    float((sub["alpha_return_1y"] > 0).mean() * 100.0), 1
                )
                if sub["alpha_return_1y"].notna().any()
                else None,
            }
        )

    return pd.DataFrame(rows)


@st.cache_data
def get_acceleration_oos_split_backtest(
    db_path,
    top_n: int = 20,
    refresh_token: int = 0,
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
    import numpy as np
    import warnings

    years = _get_complete_signal_years(db_path)
    years = [y for y in years if y >= int(train_start) and y <= int(test_end)]
    if not years:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    frames = []
    for signal_year in years:
        yr = _build_acceleration_year_frame(
            db_path, signal_year, refresh_token=refresh_token, min_spend_m=min_spend_m
        )
        if not yr.empty:
            frames.append(yr)
    if not frames:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
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
    eye[0, 0] = 0.0  # do not penalize intercept
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


@st.cache_data
def get_acceleration_simplicity_check(
    db_path,
    top_n: int = 20,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    simple_metric: str = "yoy_pct",
):
    """
    Compare composite acceleration score vs a single metric (default YoY%).
    """
    years = _get_complete_signal_years(db_path)
    rows = []
    for signal_year in years:
        yr = _build_acceleration_year_frame(
            db_path, signal_year, refresh_token=refresh_token, min_spend_m=min_spend_m
        )
        if yr.empty or simple_metric not in yr.columns:
            continue

        comp_df = yr.sort_values(["accel_score", "annual_spend"], ascending=[False, False]).head(int(top_n))
        simp_df = (
            yr[yr[simple_metric].notna()]
            .sort_values([simple_metric, "annual_spend"], ascending=[False, False])
            .head(int(top_n))
        )
        if comp_df.empty or simp_df.empty:
            continue

        row = {
            "signal_year": int(signal_year),
            "year": int(signal_year) + 1,
            "n_universe": int(len(yr)),
            "n_comp": int(len(comp_df)),
            "n_simple": int(len(simp_df)),
            "overlap_pct": round(
                100.0
                * len(set(comp_df["ticker"].tolist()) & set(simp_df["ticker"].tolist()))
                / max(int(top_n), 1),
                1,
            ),
        }
        for col in ["return_3m", "return_6m", "return_1y"]:
            comp_ret = comp_df[col].dropna()
            simp_ret = simp_df[col].dropna()
            row[f"comp_{col}"] = round(float(comp_ret.mean()), 2) if not comp_ret.empty else None
            row[f"simple_{col}"] = round(float(simp_ret.mean()), 2) if not simp_ret.empty else None
            if row[f"comp_{col}"] is not None and row[f"simple_{col}"] is not None:
                row[f"simple_minus_comp_{col}"] = round(
                    row[f"simple_{col}"] - row[f"comp_{col}"], 2
                )
            else:
                row[f"simple_minus_comp_{col}"] = None
        rows.append(row)

    annual_df = pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
    if annual_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    tol = float(getattr(config, "SIMPLICITY_ALPHA_DIFF_TOL_PCT", 0.5))
    summary = {
        "Avg overlap %": round(float(annual_df["overlap_pct"].dropna().mean()), 1)
        if annual_df["overlap_pct"].notna().any()
        else None,
        "Avg (Simple-Comp) 3M": round(float(annual_df["simple_minus_comp_return_3m"].dropna().mean()), 2)
        if annual_df["simple_minus_comp_return_3m"].notna().any()
        else None,
        "Avg (Simple-Comp) 6M": round(float(annual_df["simple_minus_comp_return_6m"].dropna().mean()), 2)
        if annual_df["simple_minus_comp_return_6m"].notna().any()
        else None,
        "Avg (Simple-Comp) 1Y": round(float(annual_df["simple_minus_comp_return_1y"].dropna().mean()), 2)
        if annual_df["simple_minus_comp_return_1y"].notna().any()
        else None,
        "Simplicity verdict": "Comparable"
        if annual_df["simple_minus_comp_return_1y"].notna().any()
        and abs(float(annual_df["simple_minus_comp_return_1y"].dropna().mean())) <= tol
        else "Composite Adds Value",
        "Tolerance (abs diff 1Y)": tol,
    }

    summary_df = pd.DataFrame([summary])
    return annual_df, summary_df


@st.cache_data
def get_sector_neutral_acceleration_backtest(
    db_path,
    top_k_per_sector: int = 1,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
):
    """
    Sector-neutral long/short test:
      - Long top-k acceleration names per sector
      - Short bottom-k acceleration names per sector
    """
    import sqlite3

    top_k = max(int(top_k_per_sector), 1)
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

    rows = []
    for signal_year in years:
        feat_df = get_acceleration_features(
            db_path,
            year=int(signal_year),
            refresh_token=refresh_token,
            min_spend_m=min_spend_m,
            ticker_only=True,
        )
        if feat_df.empty:
            continue
        ret_df = get_signal_returns(db_path, int(signal_year), refresh_token=refresh_token)
        if ret_df.empty:
            continue

        merged = feat_df.merge(
            ret_df[["ticker", "return_1m", "return_3m", "return_6m", "return_1y"]],
            on="ticker",
            how="inner",
        )
        merged = merged[merged["sector"].notna() & (merged["sector"].astype(str).str.strip() != "")]
        if merged.empty:
            continue

        long_rows = []
        short_rows = []
        sector_count = 0
        for _, sec_df in merged.groupby("sector", dropna=False):
            sec_df = sec_df.sort_values("accel_score", ascending=False)
            if len(sec_df) < (2 * top_k):
                continue
            sector_count += 1
            long_rows.append(sec_df.head(top_k))
            short_rows.append(sec_df.tail(top_k))

        if sector_count == 0:
            continue

        long_df = pd.concat(long_rows, ignore_index=True)
        short_df = pd.concat(short_rows, ignore_index=True)

        row = {
            "signal_year": int(signal_year),
            "year": int(signal_year) + 1,
            "n_sectors": int(sector_count),
            "n_long": int(len(long_df)),
            "n_short": int(len(short_df)),
        }
        for col in ["return_1m", "return_3m", "return_6m", "return_1y"]:
            long_ret = long_df[col].dropna()
            short_ret = short_df[col].dropna()
            row[f"long_{col}"] = round(float(long_ret.mean()), 2) if not long_ret.empty else None
            row[f"short_{col}"] = round(float(short_ret.mean()), 2) if not short_ret.empty else None
            if row[f"long_{col}"] is not None and row[f"short_{col}"] is not None:
                row[f"ls_{col}"] = round(row[f"long_{col}"] - row[f"short_{col}"], 2)
            else:
                row[f"ls_{col}"] = None
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)


@st.cache_data
def get_policy_risk_regime_features(signal_years, refresh_token: int = 0):
    """
    Build macro/policy-risk regime flags at each signal date:
      - SPY below 200DMA
      - VIX above threshold
      - Credit spreads proxy widening (LQD/HYG ratio rising)
      - Recession-risk proxy rising (10Y-3M term spread deteriorating/inverted)
    """
    import yfinance as yf

    if not signal_years:
        return pd.DataFrame()

    min_signal_year = int(min(signal_years))
    max_signal_year = int(max(signal_years))
    start = f"{min_signal_year - 2}-01-01"
    end = f"{max_signal_year + 2}-12-31"

    symbols = ["SPY", "^VIX", "LQD", "HYG", "^TNX", "^IRX"]
    market = {}
    for sym in symbols:
        try:
            df = yf.download(
                sym,
                start=start,
                end=end,
                progress=False,
                auto_adjust=True,
            )
            if df.empty:
                continue
            close = df["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close.index = pd.to_datetime(close.index).tz_localize(None)
            market[sym] = close.sort_index()
        except Exception:
            continue

    if "SPY" not in market:
        return pd.DataFrame()

    spy = market["SPY"].copy()
    spy_200dma = spy.rolling(200, min_periods=180).mean()
    vix = market.get("^VIX")
    lqd = market.get("LQD")
    hyg = market.get("HYG")
    tnx = market.get("^TNX")
    irx = market.get("^IRX")

    # Credit-spread proxy: safer credit (LQD) outperforming high-yield (HYG)
    # implies spread widening / risk-off conditions.
    credit_ratio = None
    credit_delta = None
    credit_lb = int(getattr(config, "REGIME_CREDIT_LOOKBACK_DAYS", 63))
    if lqd is not None and hyg is not None:
        common_idx = lqd.index.intersection(hyg.index)
        if not common_idx.empty:
            ratio = (lqd.reindex(common_idx) / hyg.reindex(common_idx)).dropna()
            if not ratio.empty:
                credit_ratio = ratio
                credit_delta = ratio - ratio.shift(credit_lb)

    term_spread = None
    term_delta = None
    if tnx is not None and irx is not None:
        common_idx = tnx.index.intersection(irx.index)
        if not common_idx.empty:
            spread = (tnx.reindex(common_idx) - irx.reindex(common_idx)).dropna()
            if not spread.empty:
                term_spread = spread
                term_delta = spread - spread.shift(63)

    def _last_val(series, asof_ts):
        if series is None or series.empty:
            return None
        data = series[series.index <= asof_ts]
        if data.empty:
            return None
        return float(data.iloc[-1])

    vix_threshold = float(getattr(config, "REGIME_VIX_THRESHOLD", 25.0))
    rows = []
    for year in sorted(set(int(y) for y in signal_years)):
        signal_date, _, _ = _q4_signal_window(year, window_days=46)
        signal_ts = pd.to_datetime(signal_date)

        spy_px = _last_val(spy, signal_ts)
        spy_dma = _last_val(spy_200dma, signal_ts)
        vix_val = _last_val(vix, signal_ts)
        credit_d = _last_val(credit_delta, signal_ts)
        term_val = _last_val(term_spread, signal_ts)
        term_d = _last_val(term_delta, signal_ts)

        spx_below_200dma = (
            bool(spy_px < spy_dma) if spy_px is not None and spy_dma is not None else None
        )
        vix_gt = bool(vix_val > vix_threshold) if vix_val is not None else None
        credit_widen = bool(credit_d > 0) if credit_d is not None else None
        recession_risk_rising = (
            bool((term_val is not None and term_val < 0) or (term_d is not None and term_d < 0))
            if term_val is not None or term_d is not None
            else None
        )

        bool_flags = [
            f for f in [spx_below_200dma, vix_gt, credit_widen, recession_risk_rising]
            if f is not None
        ]
        risk_off_count = int(sum(1 for f in bool_flags if f)) if bool_flags else None
        high_policy_risk = (
            bool(risk_off_count >= 2) if risk_off_count is not None else None
        )

        rows.append(
            {
                "signal_year": int(year),
                "signal_date": signal_date,
                "spy_px": spy_px,
                "spy_200dma": spy_dma,
                "vix": vix_val,
                "term_spread": term_val,
                "spx_below_200dma": spx_below_200dma,
                "vix_gt_25": vix_gt,
                "credit_spreads_widening": credit_widen,
                "recession_risk_rising": recession_risk_rising,
                "risk_off_count": risk_off_count,
                "high_policy_risk": high_policy_risk,
            }
        )

    return pd.DataFrame(rows)


@st.cache_data
def get_regime_conditioned_acceleration_performance(
    db_path,
    top_n: int = 20,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
):
    bt_df, _ = get_acceleration_event_backtest(
        db_path,
        top_n=top_n,
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
    )
    if bt_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    regime_df = get_policy_risk_regime_features(
        bt_df["signal_year"].tolist(), refresh_token=refresh_token
    )
    if regime_df.empty:
        return bt_df, pd.DataFrame()

    merged = bt_df.merge(regime_df, on="signal_year", how="left")

    checks = [
        ("spx_below_200dma", "SPX < 200DMA"),
        ("vix_gt_25", "VIX > 25"),
        ("credit_spreads_widening", "Credit Spreads Widening"),
        ("recession_risk_rising", "Recession Risk Rising"),
        ("high_policy_risk", "High Policy Risk (2+ flags)"),
    ]
    rows = []
    for col, label in checks:
        if col not in merged.columns:
            continue
        valid = merged[merged[col].notna()].copy()
        if valid.empty:
            continue
        for state in [True, False]:
            sub = valid[valid[col] == state]
            if sub.empty:
                continue
            rows.append(
                {
                    "Regime": label,
                    "State": "True" if state else "False",
                    "Years": int(len(sub)),
                    "Avg Top 3M": round(float(sub["top_return_3m"].dropna().mean()), 2)
                    if sub["top_return_3m"].notna().any()
                    else None,
                    "Avg Top 6M": round(float(sub["top_return_6m"].dropna().mean()), 2)
                    if sub["top_return_6m"].notna().any()
                    else None,
                    "Avg Top 1Y": round(float(sub["top_return_1y"].dropna().mean()), 2)
                    if sub["top_return_1y"].notna().any()
                    else None,
                    "Avg Alpha 1Y (vs Univ)": round(float(sub["alpha_return_1y"].dropna().mean()), 2)
                    if sub["alpha_return_1y"].notna().any()
                    else None,
                    "Avg Alpha 1Y (vs SPX)": round(float(sub["alpha_vs_spx_1y"].dropna().mean()), 2)
                    if sub["alpha_vs_spx_1y"].notna().any()
                    else None,
                }
            )

    return merged, pd.DataFrame(rows)


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


def main():
    _configure_streamlit_page()

    if "cache_buster" not in st.session_state:
        st.session_state["cache_buster"] = 0

    # Header
    st.markdown("<h1 style='text-align: center;'>US Lobbying Equities Long Only Strategy</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #a0a0a0;'>Institutional-Grade Lobbying Disclosure Analytics</p>", unsafe_allow_html=True)
    
    # Initialize tools
    fetcher = get_data_fetcher()
    
    # Get years that actually have data
    available_years = get_available_years(fetcher.db_path, st.session_state["cache_buster"])
    default_year = get_default_year(fetcher.db_path, st.session_state["cache_buster"])
    try:
        default_year_index = available_years.index(default_year)
    except ValueError:
        default_year_index = 0
    selected_year_complete = None
    selected_ingestion_status = None
    
    # Sidebar
    with st.sidebar:
        st.markdown("###  Controls")
        
        selected_year = st.selectbox(
            "Year",
            options=available_years,
            index=default_year_index
        )
        
        selected_quarter = st.selectbox(
            "Quarter",
            options=['Q1', 'Q2', 'Q3', 'Q4', 'YTD', 'Full Year'],
            index=5  # Default to Full Year
        )
        
        market_cap_filter = st.multiselect(
            "Market Cap",
            options=['Large Cap (>$10B)', 'Mid Cap ($2B-$10B)', 'Small Cap (<$2B)'],
            default=['Large Cap (>$10B)', 'Mid Cap ($2B-$10B)', 'Small Cap (<$2B)']
        )
        
        available_sectors = get_available_sectors(fetcher.db_path, st.session_state["cache_buster"])
        sector_filter = st.multiselect(
            "Sector",
            options=available_sectors,
            default=[]
        )
        
        st.markdown("---")
        
        min_spend = st.number_input(
            "Min Lobbying Spend ($M)",
            min_value=0.0,
            max_value=100.0,
            value=1.0,
            step=0.5
        )
        
        st.markdown("---")
        
        if st.button(" Refresh Data", use_container_width=True):
            st.session_state["cache_buster"] += 1
            st.rerun()
        
        st.markdown("---")
        
        # Add pipeline refresh button
        with st.expander("Advanced: Fetch / Refresh Data"):
            st.markdown("**Fetch latest lobbying filings from Senate database**")
            st.info(
                f"This will **fully re-fetch** all filings for {selected_year} and rebuild the "
                f"database for that year. Use this to fix incomplete years (e.g. 2024 fetched "
                f"mid-year) or to pull the latest filings. May take 5–15 minutes."
            )
            
            if st.button(" Fetch Data for Selected Year", use_container_width=True):
                with st.spinner(f"Fetching {selected_year} data from Senate database..."):
                    try:
                        import sys
                        import os
                        sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
                        from build_company_lobbying import build_company_lobbying_for_year
                        
                        # Run the pipeline in the UI
                        build_company_lobbying_for_year(
                            selected_year,
                            db_path=fetcher.db_path,
                            force=True,
                        )
                        
                        st.success(f"✅ Successfully fetched and processed {selected_year} data!")
                        st.info("Refreshing app with new data...")
                        
                        # Bump refresh token and reload
                        st.session_state["cache_buster"] += 1
                        time.sleep(2)
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f" Error fetching data: {e}")
                        st.info("You can also run the pipeline from command line: `python src/build_company_lobbying.py`")
        
        st.markdown("---")

        with st.expander("Advanced: Enrich Existing Data"):
            st.markdown("**Backfill ticker & market-cap for unmatched companies**")
            st.caption(
                "Runs the improved CompanyMapper (with fuzzy matching) over all rows "
                "that currently have no ticker — no Senate API call required."
            )
            if st.button("Run Ticker Backfill", use_container_width=True):
                with st.spinner("Scanning unmatched companies…"):
                    try:
                        from build_company_lobbying import backfill_ticker_mappings
                        result = backfill_ticker_mappings(db_path=fetcher.db_path)
                        st.success(
                            f"Backfill complete: checked {result['checked']:,} names, "
                            f"matched {result['matched']:,} new tickers."
                        )
                        method_counts = result.get("method_counts") or {}
                        if method_counts:
                            method_df = pd.DataFrame(
                                [
                                    {"Match Method": m, "Count": c}
                                    for m, c in sorted(
                                        method_counts.items(),
                                        key=lambda x: (-x[1], x[0]),
                                    )
                                ]
                            )
                            st.caption("Backfill match-method breakdown")
                            st.dataframe(
                                method_df,
                                use_container_width=True,
                                hide_index=True,
                                height=min(220, 40 + 35 * len(method_df)),
                            )
                        st.session_state["cache_buster"] += 1
                    except Exception as e:
                        st.error(f"Backfill error: {e}")

            st.markdown("---")
            st.markdown("**Revalidate existing ticker assignments**")
            st.caption(
                "Re-checks every ticker in the database against the current stricter "
                "CompanyMapper (0.82 cutoff + token-overlap guard). Stale false-positive "
                "fuzzy matches from the old 0.72-era logic are nullified."
            )

            # ── Step 1: Preview (always safe, read-only) ─────────────────────
            if st.button("Preview Changes (dry-run)", use_container_width=True, key="rv_preview"):
                with st.spinner("Evaluating all existing assignments…"):
                    preview_df = _get_revalidation_preview(
                        fetcher.db_path, st.session_state["cache_buster"]
                    )
                st.session_state["rv_preview_df"] = preview_df

            if "rv_preview_df" in st.session_state:
                preview_df = st.session_state["rv_preview_df"]
                if preview_df.empty:
                    st.success("All existing ticker assignments look correct — nothing to change.")
                else:
                    nullify_n = (preview_df["Action"] == "NULLIFY").sum()
                    remap_n   = (preview_df["Action"] == "REMAP").sum()
                    st.warning(
                        f"**{nullify_n:,} assignments would be nullified** "
                        f"(bad fuzzy matches) · **{remap_n:,} would be remapped** "
                        f"(corrected ticker). Review below before committing."
                    )

                    # Colour-code by action for readability
                    action_filter = st.selectbox(
                        "Show",
                        ["All changes", "NULLIFY only", "REMAP only"],
                        key="rv_action_filter",
                    )
                    show_df = preview_df.copy()
                    if action_filter == "NULLIFY only":
                        show_df = show_df[show_df["Action"] == "NULLIFY"]
                    elif action_filter == "REMAP only":
                        show_df = show_df[show_df["Action"] == "REMAP"]

                    st.dataframe(show_df, use_container_width=True, hide_index=True, height=350)
                    st.download_button(
                        "Download Preview CSV",
                        data=preview_df.to_csv(index=False).encode("utf-8"),
                        file_name="revalidation_preview.csv",
                        mime="text/csv",
                        key="dl_rv_preview",
                    )

                    st.markdown("---")
                    st.markdown(
                        "**Ready to commit?** Inspect the list above.  \n"
                        "Legitimate matches that shouldn't be nullified should be added "
                        "to **Settings → Custom Ticker Mappings** before running — "
                        "they'll be persisted in `entity_aliases` and preserved by lookup."
                    )
                    # ── Step 2: Commit button (write) ─────────────────────────
                    if st.button(
                        f"Commit — nullify {nullify_n:,} + remap {remap_n:,}",
                        use_container_width=True,
                        type="primary",
                        key="rv_commit",
                    ):
                        with st.spinner("Writing changes to database…"):
                            try:
                                from build_company_lobbying import revalidate_ticker_mappings
                                rv = revalidate_ticker_mappings(
                                    db_path=fetcher.db_path, dry_run=False
                                )
                                st.success(
                                    f"Done — nullified {rv['nullified']:,}, "
                                    f"remapped {rv['changed']:,}, "
                                    f"unchanged {rv['unchanged']:,}."
                                )
                                remap_methods = rv.get("remap_method_counts") or {}
                                if remap_methods:
                                    remap_df = pd.DataFrame(
                                        [
                                            {"Remap Method": m, "Count": c}
                                            for m, c in sorted(
                                                remap_methods.items(),
                                                key=lambda x: (-x[1], x[0]),
                                            )
                                        ]
                                    )
                                    st.caption("Revalidation remap-method breakdown")
                                    st.dataframe(
                                        remap_df,
                                        use_container_width=True,
                                        hide_index=True,
                                        height=min(180, 40 + 35 * len(remap_df)),
                                    )
                                # Clear cached preview so next run re-reads DB
                                del st.session_state["rv_preview_df"]
                                st.session_state["cache_buster"] += 1
                            except Exception as e:
                                st.error(f"Revalidation error: {e}")

            st.markdown("---")
            st.markdown("**Populate stock performance table**")
            st.caption(
                "For every matched ticker, fetches price history and calculates "
                "forward returns (1m, 3m, 6m, 1y) anchored to lag-adjusted "
                "quarter signals. Run this after a backfill to unlock signal validation."
            )
            st.caption(
                f"Current filing lag assumption: {int(getattr(config, 'LDA_FILING_LAG_DAYS', 20))} day(s). "
                "If you change this in Settings, repopulate stock performance."
            )
            lookback = st.slider("Years of history", min_value=1, max_value=7, value=7, key="perf_lookback")
            if st.button("Populate Stock Performance", use_container_width=True):
                with st.spinner("Fetching price history from Yahoo Finance… this may take several minutes."):
                    try:
                        from build_company_lobbying import populate_stock_performance
                        result = populate_stock_performance(db_path=fetcher.db_path, lookback_years=lookback)
                        st.success(
                            f"Done: {result['tickers_processed']} tickers, "
                            f"{result['rows_inserted']} rows inserted, "
                            f"{result['errors']} errors."
                        )
                        st.session_state["cache_buster"] += 1
                    except Exception as e:
                        st.error(f"Error: {e}")

        st.markdown("---")
        
        # Data availability indicator
        st.markdown("####  Data Status")
        ingestion_df = get_latest_ingestion_status(
            fetcher.db_path, st.session_state["cache_buster"]
        )
        
        # Get data counts
        conn = sqlite3.connect(fetcher.db_path)
        
        # Check what we have for selected year
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM company_lobbying WHERE year = ?", (selected_year,))
        year_count = cursor.fetchone()[0]
        
        # Check quarters available
        cursor.execute("SELECT DISTINCT quarter FROM company_lobbying WHERE year = ? ORDER BY quarter", (selected_year,))
        quarters = [row[0] for row in cursor.fetchall()]
        
        # Total companies
        cursor.execute("SELECT COUNT(DISTINCT company_name) FROM company_lobbying")
        total_companies = cursor.fetchone()[0]
        
        conn.close()
        
        if year_count > 0:
            run_row = ingestion_df[ingestion_df["year"] == selected_year] if not ingestion_df.empty else pd.DataFrame()
            if not run_row.empty:
                row = run_row.iloc[0]
                status = str(row.get("status") or "unknown")
                is_complete_raw = row.get("is_complete")
                selected_ingestion_status = status

                if pd.isna(is_complete_raw):
                    selected_year_complete = None
                    st.warning(
                        f"{selected_year}: {year_count:,} records "
                        f"(status: {status.replace('_', ' ')})"
                    )
                    st.caption(
                        "No ingestion audit row for this year yet. "
                        "Run a force refresh once to populate ingestion metadata."
                    )
                elif int(is_complete_raw) == 1:
                    selected_year_complete = True
                    st.success(f"{selected_year}: {year_count:,} records (ingestion complete)")
                else:
                    selected_year_complete = False
                    st.error(
                        f"{selected_year}: {year_count:,} records (last run: {status})"
                    )
                    st.caption("Re-fetch this year via 'Advanced: Fetch / Refresh Data' above.")

                api_count = row.get("api_reported_count")
                fetched = row.get("fetched_count")
                if pd.notna(api_count) and pd.notna(fetched):
                    st.caption(f"API reported {int(api_count):,} filings · fetched {int(fetched):,}")
                notes = row.get("notes")
                if isinstance(notes, str) and notes.strip() and notes.strip().lower() != "complete":
                    st.caption(f"Run notes: {notes}")
                latest_attempt = row.get("latest_attempt_status")
                if (
                    isinstance(latest_attempt, str)
                    and latest_attempt.strip()
                    and latest_attempt != status
                ):
                    st.caption(
                        f"Latest attempt status: {latest_attempt} "
                        "(snapshot status shown above)"
                    )
            else:
                selected_year_complete = None
                selected_ingestion_status = None
                if year_count < 10_000:
                    st.warning(f"{selected_year}: {year_count:,} records (status unknown)")
                    st.caption("Run a force refresh once to create ingestion metadata for this year.")
                else:
                    st.success(f"{selected_year}: {year_count:,} records")
            if quarters:
                st.caption(f"Quarters: {', '.join(quarters)}")
        else:
            st.warning(f"{selected_year}: No data yet")
            st.caption("Click 'Fetch / Refresh Data' above to load it")
        
        st.metric("Total Companies in DB", f"{total_companies:,}")
        
        st.markdown("---")
        
        st.markdown("#### Data Sources")
        st.markdown("- Senate Lobbying Disclosure")
        st.markdown("- OpenSecrets.org")
        st.markdown("- Yahoo Finance")
        
    # Main content tabs
    if selected_year_complete is False:
        st.error(
            f"Selected year {selected_year} is not marked complete (last run status: {selected_ingestion_status}). "
            "Signals may be biased by partial data until this year is fully refreshed."
        )

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        " Overview", " Top Lobbyists", "Performance",
        "Signals", "Company Research", " Settings"
    ])
    
    with tab1:
        st.markdown("### Strategy Metrics")

        # Get filtered data first so metrics are grounded in live DB values.
        holdings_df = get_filtered_data(
            fetcher, selected_year, selected_quarter, market_cap_filter, sector_filter, min_spend
        )

        total_companies = len(holdings_df)
        total_spend = holdings_df["total_lobbying_spend"].sum() if total_companies > 0 else 0
        median_spend = holdings_df["total_lobbying_spend"].median() if total_companies > 0 else 0
        ticker_coverage = (
            (holdings_df["ticker"].fillna("").str.strip() != "").mean() * 100
            if total_companies > 0
            else 0
        )
        mcap_coverage = (
            holdings_df["market_cap"].notna().mean() * 100 if total_companies > 0 else 0
        )

        st.caption("Live metrics from your database for the currently selected filters.")

        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("Companies", f"{total_companies:,}")
        with col2:
            st.metric("Total Lobbying Spend", format_currency(total_spend))
        with col3:
            st.metric("Median Spend", format_currency(median_spend))
        with col4:
            st.metric("Ticker Coverage", f"{ticker_coverage:.1f}%")
        with col5:
            st.metric("Market Cap Coverage", f"{mcap_coverage:.1f}%")

        st.markdown("---")

        # ── Data Trust Panel ──────────────────────────────────────────────────
        with st.expander("Data Quality", expanded=False):
            import sqlite3 as _dt_sq3
            _dt_conn = _dt_sq3.connect(fetcher.db_path)
            try:
                _dt_total = pd.read_sql_query(
                    """
                    SELECT COUNT(*) AS n
                    FROM company_lobbying
                    WHERE year = ?
                      AND total_lobbying_spend > 0
                    """,
                    _dt_conn, params=(selected_year,)
                ).iloc[0]["n"]
                _dt_mapped = pd.read_sql_query(
                    """
                    SELECT COUNT(*) AS n
                    FROM company_lobbying
                    WHERE year = ?
                      AND total_lobbying_spend > 0
                      AND ticker IS NOT NULL
                      AND ticker != ''
                    """,
                    _dt_conn, params=(selected_year,)
                ).iloc[0]["n"]
                _dt_unmatched = pd.read_sql_query(
                    """
                    SELECT COUNT(DISTINCT company_name) AS n
                    FROM company_lobbying
                    WHERE year = ?
                      AND total_lobbying_spend > 0
                      AND (ticker IS NULL OR ticker = '')
                    """,
                    _dt_conn, params=(selected_year,)
                ).iloc[0]["n"]
                _dt_last_run = pd.read_sql_query(
                    """SELECT MAX(COALESCE(finished_at, started_at)) AS last_run
                       FROM ingestion_runs WHERE year = ?""",
                    _dt_conn, params=(selected_year,)
                ).iloc[0]["last_run"]
                if _dt_last_run is None:
                    _dt_last_run = pd.read_sql_query(
                        """SELECT MAX(last_updated) AS last_run
                           FROM company_lobbying WHERE year = ?""",
                        _dt_conn,
                        params=(selected_year,),
                    ).iloc[0]["last_run"]
                # New filings ingested in last 7 days
                _week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                _dt_new_week = pd.read_sql_query(
                    """SELECT COUNT(*) AS n FROM ingestion_runs
                       WHERE year = ? AND started_at >= ?""",
                    _dt_conn, params=(selected_year, _week_ago)
                ).iloc[0]["n"]
            except Exception:
                _dt_total = _dt_mapped = _dt_unmatched = 0
                _dt_last_run = None
                _dt_new_week = 0
            finally:
                _dt_conn.close()

            _dt_match_rate = (_dt_mapped / _dt_total * 100) if _dt_total > 0 else 0.0
            dt1, dt2, dt3, dt4 = st.columns(4)
            dt1.metric(
                "Ticker Match Rate",
                f"{_dt_match_rate:.1f}%",
                help="Rows with a mapped public ticker ÷ total rows for this year.",
            )
            dt2.metric(
                "Unmatched Entities",
                f"{_dt_unmatched:,}",
                help="Distinct company names with no ticker match for this year.",
            )
            dt3.metric(
                "Ingestion Runs (7d)",
                f"{_dt_new_week}",
                help="Number of data fetch runs completed in the last 7 days.",
            )
            dt4.metric(
                "Last Refresh",
                str(_dt_last_run)[:16] if _dt_last_run else "Never",
                help="Timestamp of the most recent completed ingestion run for this year.",
            )
            if _dt_unmatched > 0:
                st.caption(
                    f"{_dt_unmatched:,} company names have no ticker mapping. "
                    "Use Settings → Custom Ticker Mappings to fill gaps."
                )

        st.markdown("---")

        yearly_summary = get_yearly_summary(fetcher.db_path, st.session_state["cache_buster"])
        if yearly_summary.empty:
            st.info("No historical company-level data available yet.")
        else:
            # Keep only completed full years (exclude current in-progress year).
            full_year_cutoff = datetime.now().year - 1
            ys = yearly_summary[yearly_summary["year"] <= full_year_cutoff].copy()
            if ys.empty:
                ys = yearly_summary.copy()
            ys_years = ys["year"].tolist()

            # Compute YoY% on total spend
            ys["yoy_pct"] = ys["total_lobbying_spend"].pct_change().multiply(100).round(1)

            # ── Chart: Spend bars + YoY% line + Company count ─────────────
            st.markdown("#### Total Lobbying Spend — All Years")
            summary_chart = go.Figure()

            summary_chart.add_trace(go.Bar(
                x=ys_years,
                y=(ys["total_lobbying_spend"] / 1_000_000).round(1).tolist(),
                name="Total Spend ($M)",
                marker_color="#60a5fa",
                text=(ys["total_lobbying_spend"] / 1_000_000).round(1).apply(
                    lambda v: f"${v:.0f}M"
                ).tolist(),
                textposition="outside",
                hovertemplate="Year %{x}<br>Spend: $%{y:.1f}M<extra></extra>",
            ))

            # YoY% line on secondary axis
            valid_yoy = ys["yoy_pct"].notna()
            if valid_yoy.any():
                yoy_colors = [
                    "#22c55e" if (pd.notna(v) and v >= 0) else "#ef4444"
                    for v in ys["yoy_pct"]
                ]
                summary_chart.add_trace(go.Scatter(
                    x=ys_years,
                    y=ys["yoy_pct"].tolist(),
                    name="YoY% Change",
                    mode="lines+markers+text",
                    line=dict(color="#f97316", width=2.5, dash="dot"),
                    marker=dict(size=8, color=yoy_colors),
                    text=[
                        f"{v:+.1f}%" if pd.notna(v) else ""
                        for v in ys["yoy_pct"]
                    ],
                    textposition="top center",
                    textfont=dict(size=11),
                    yaxis="y2",
                    hovertemplate="YoY: %{y:+.1f}%<extra></extra>",
                ))

            summary_chart.add_hline(
                y=0, line_color="#6b7280", line_width=1, line_dash="solid",
                annotation=None,
            )
            summary_chart.update_layout(
                xaxis=dict(
                    title="Year", dtick=1, tickvals=ys_years,
                    gridcolor="#2d3748", showgrid=True,
                ),
                yaxis=dict(
                    title="Total Spend ($M)",
                    gridcolor="#2d3748", showgrid=True,
                    tickformat="$,.0f",
                ),
                yaxis2=dict(
                    title="YoY% Change",
                    overlaying="y", side="right",
                    showgrid=False,
                    zeroline=True, zerolinecolor="#6b7280",
                ),
                plot_bgcolor="#0e1117",
                paper_bgcolor="#0e1117",
                font=dict(color="#ffffff", size=12),
                legend=dict(
                    bgcolor="#1a1d24", bordercolor="#4a5568", borderwidth=1,
                    orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1,
                ),
                height=420,
                margin=dict(l=0, r=60, t=50, b=0),
                barmode="group",
            )
            st.plotly_chart(summary_chart, use_container_width=True,
                            key="overview_annual_chart")

            # ── KPI row: coverage stats across all years ───────────────────
            ov_c1, ov_c2, ov_c3, ov_c4, ov_c5 = st.columns(5)
            with ov_c1:
                st.metric("Years of Data", f"{len(ys_years)}")
            with ov_c2:
                st.metric(
                    "Total Spend (all years)",
                    f"${ys['total_lobbying_spend'].sum()/1e9:.1f}B",
                )
            with ov_c3:
                peak_yr = int(ys.loc[ys["total_lobbying_spend"].idxmax(), "year"])
                st.metric("Peak Spend Year", str(peak_yr))
            with ov_c4:
                avg_yoy = ys["yoy_pct"].dropna().mean()
                st.metric(
                    "Avg Annual Growth",
                    f"{avg_yoy:+.1f}%" if pd.notna(avg_yoy) else "N/A",
                )
            with ov_c5:
                latest_companies = int(ys.iloc[-1]["unique_entities"])
                st.metric("Companies (latest yr)", f"{latest_companies:,}")

            # ── Sector spend trend (completed years) ───────────────────────
            st.markdown("---")
            st.markdown("#### Sector Lobbying Spend Trend")
            st.caption(
                "Annual lobbying spend by sector across all years. "
                "Highlights which industries are increasing regulatory engagement over time."
            )
            sector_trend_df = get_sector_spend_by_year(
                fetcher.db_path,
                refresh_token=st.session_state["cache_buster"],
            )
            if not sector_trend_df.empty:
                sector_trend_df = sector_trend_df[
                    sector_trend_df["year"] <= (datetime.now().year - 1)
                ]
                sector_palette = [
                    "#60a5fa", "#f97316", "#22c55e", "#a78bfa",
                    "#f43f5e", "#34d399", "#fbbf24", "#38bdf8",
                    "#e879f9", "#fb923c", "#84cc16",
                ]
                sectors_ordered = (
                    sector_trend_df.groupby("sector")["spend"]
                    .sum()
                    .sort_values(ascending=False)
                    .index.tolist()
                )
                fig_sec_trend = go.Figure()
                for i, sec in enumerate(sectors_ordered):
                    sec_data = sector_trend_df[sector_trend_df["sector"] == sec]
                    fig_sec_trend.add_trace(go.Scatter(
                        x=sec_data["year"].tolist(),
                        y=(sec_data["spend"] / 1_000_000).round(1).tolist(),
                        name=sec,
                        mode="lines+markers",
                        line=dict(
                            color=sector_palette[i % len(sector_palette)],
                            width=2,
                        ),
                        marker=dict(size=6),
                        hovertemplate=(
                            f"{sec}<br>Year %{{x}}: $%{{y:.1f}}M<extra></extra>"
                        ),
                    ))
                fig_sec_trend.update_layout(
                    xaxis=dict(
                        title="Year", dtick=1,
                        tickvals=sorted(sector_trend_df["year"].unique().tolist()),
                        gridcolor="#2d3748",
                    ),
                    yaxis=dict(
                        title="Annual Spend ($M)",
                        gridcolor="#2d3748",
                        tickformat="$,.0f",
                    ),
                    plot_bgcolor="#0e1117",
                    paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    legend=dict(
                        bgcolor="#1a1d24", bordercolor="#4a5568", borderwidth=1,
                        orientation="h", yanchor="bottom", y=1.02,
                        xanchor="right", x=1,
                    ),
                    height=420,
                    margin=dict(l=0, r=40, t=50, b=0),
                )
                st.plotly_chart(fig_sec_trend, use_container_width=True,
                                key="overview_sector_trend")
        
        # Current holdings summary
        st.markdown("### Current Strategy Holdings")

        if len(holdings_df) == 0:
            st.warning("No holdings match your current filters. Adjust filters in the sidebar.")
        else:
            # Top Lobbying Yield highlight
            valid_ratios = holdings_df[holdings_df['spend_to_mcap_ratio'].notna() & (holdings_df['market_cap'].notna())]
            
            if len(valid_ratios) > 0:
                best_row = valid_ratios.sort_values('spend_to_mcap_ratio', ascending=False).iloc[0]
                
                st.markdown("####  Top Lobbying Yield (Spend / Market Cap)")
                
                col_a, col_b, col_c, col_d = st.columns(4)
                with col_a:
                    st.metric("Company", best_row['company_name'][:25] + "..." if len(best_row['company_name']) > 25 else best_row['company_name'])
                with col_b:
                    best_ticker = (
                        best_row["ticker"]
                        if pd.notna(best_row["ticker"]) and str(best_row["ticker"]).strip()
                        else "N/A"
                    )
                    st.metric("Ticker", best_ticker)
                with col_c:
                    st.metric("Spend/MCap %", f"{best_row['spend_to_mcap_ratio']*100:.4f}%" if pd.notna(best_row['spend_to_mcap_ratio']) else "N/A")
                with col_d:
                    st.metric("Lobbying Spend", format_currency(best_row['total_lobbying_spend']))
                
                st.markdown("---")
            
            # Show top holdings
            display_df = holdings_df.head(20).copy()
            display_df["ticker"] = display_df["ticker"].fillna("N/A")
            display_df['Lobbying Spend'] = display_df['total_lobbying_spend'].apply(format_currency)
            display_df['Market Cap'] = display_df['market_cap'].apply(format_currency)
            display_df['Spend/MCap %'] = display_df['spend_to_mcap_ratio'].apply(lambda x: f"{x*100:.3f}%" if pd.notna(x) else "N/A")
            display_df['Quarters'] = display_df['quarters_covered'].replace('', 'N/A')
            display_df['Rows Merged'] = display_df['source_rows']
            
            # Select columns
            display_df = display_df[
                ['company_name', 'ticker', 'Lobbying Spend', 'Market Cap', 'Spend/MCap %', 'Quarters', 'Rows Merged', 'sector']
            ]
            display_df.columns = ['Company', 'Ticker', 'Lobbying Spend', 'Market Cap', 'Spend/MCap %', 'Quarters', 'Rows Merged', 'Sector']
        
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                height=400
            )

            # Download button — export the raw numeric data (not the formatted display version)
            raw_export = holdings_df.copy()
            raw_export["ticker"] = raw_export["ticker"].fillna("")
            st.download_button(
                label="Download Holdings CSV",
                data=raw_export.to_csv(index=False).encode("utf-8"),
                file_name=f"holdings_{selected_year}_{selected_quarter}.csv",
                mime="text/csv",
            )

        # ── Strategy vs S&P 500 Benchmark (promoted from Performance tab) ──────
        st.markdown("---")
        st.markdown("#### Strategy vs. S&P 500 — Annual Returns")
        _render_benchmark_section(
            fetcher.db_path,
            cache_buster=st.session_state["cache_buster"],
            key_prefix="tab1_bench",
        )

    with tab2:
        st.markdown(f"### Top Lobbying Spenders — {selected_year}")

        with st.expander("Active Filters", expanded=False):
            st.write(f"**Year:** {selected_year} | **Quarter:** {selected_quarter} | **Min Spend:** ${min_spend}M")
            st.write(f"**Market Cap:** {market_cap_filter or 'All'} | **Sector:** {sector_filter or 'All'}")

        # ── Load base + enrichment data ────────────────────────────────────────
        filtered_df = get_filtered_data(
            fetcher, selected_year, selected_quarter,
            market_cap_filter, sector_filter, min_spend
        )
        enriched_df = get_enriched_leaderboard(
            fetcher.db_path, selected_year,
            refresh_token=st.session_state["cache_buster"]
        )

        # Merge enrichment onto filtered data
        if (
            not filtered_df.empty
            and not enriched_df.empty
            and "entity_key" in filtered_df.columns
        ):
            filtered_df = filtered_df.merge(
                enriched_df, on="entity_key", how="left"
            )
        elif not filtered_df.empty:
            for col in ["yoy_pct", "qoq_pct", "is_spike", "is_consistent", "accel_vs_sector"]:
                filtered_df[col] = None

        st.caption(
            f"{len(filtered_df):,} companies matched your filters "
            + (f"({int(filtered_df['source_rows'].sum()):,} source rows aggregated)"
               if not filtered_df.empty and "source_rows" in filtered_df.columns else "")
        )

        col1, col2 = st.columns([3, 1])

        with col1:
            st.markdown("#### Leaderboard")

            if filtered_df.empty:
                st.warning("No data matches your filters. Try adjusting the filters in the sidebar.")
            else:
                # ── Sort picker ─────────────────────────────────────────────────
                sort_options = {
                    "YoY %":          ("yoy_pct",              False),
                    "Absolute Spend": ("total_lobbying_spend",  False),
                    "QoQ %":          ("qoq_pct",              False),
                    "Accel vs Sector":("accel_vs_sector",       False),
                    "Spend/MCap":     ("spend_to_mcap_ratio",   False),
                }
                sort_choice = st.selectbox(
                    "Sort by",
                    list(sort_options.keys()),
                    index=0,
                    key="tab2_sort",
                )
                sort_col, sort_asc = sort_options[sort_choice]

                # Apply sort (put NaN at bottom)
                work_df = filtered_df.copy()
                if sort_col in work_df.columns:
                    work_df = work_df.sort_values(
                        sort_col, ascending=sort_asc, na_position="last"
                    ).reset_index(drop=True)

                # ── Build display columns ───────────────────────────────────────
                table_limit = 1000
                disp = work_df.head(table_limit).copy()
                disp["ticker"] = disp["ticker"].fillna("N/A")
                disp["Rank"]        = range(1, len(disp) + 1)
                disp["Company"]     = disp["company_name"]
                disp["Ticker"]      = disp["ticker"]
                disp["Total Spend"] = disp["total_lobbying_spend"].apply(format_currency)
                disp["Market Cap"]  = disp["market_cap"].apply(format_currency)
                disp["Spend/MCap"]  = disp["spend_to_mcap_ratio"].apply(
                    lambda x: f"{x*100:.3f}%" if pd.notna(x) else "N/A"
                )
                disp["Sector"]      = disp["sector"].fillna("N/A")

                # YoY % with sign
                def _fmt_pct(v):
                    if pd.isna(v):
                        return "N/A"
                    return f"{'+' if v >= 0 else ''}{v:.1f}%"

                disp["YoY %"]            = disp["yoy_pct"].apply(_fmt_pct)
                disp["QoQ %"]            = disp["qoq_pct"].apply(_fmt_pct)
                disp["Accel vs Sector"]  = disp["accel_vs_sector"].apply(_fmt_pct)

                # Flag columns as readable text
                def _spike_label(v):
                    return "SPIKE" if v else ""

                def _consistent_label(v):
                    return "YES" if v else ""

                disp["Spike"]       = disp["is_spike"].apply(_spike_label)
                disp["Consistent"]  = disp["is_consistent"].apply(_consistent_label)

                display_cols = [
                    "Rank", "Company", "Ticker", "Total Spend",
                    "YoY %", "QoQ %", "Accel vs Sector",
                    "Spike", "Consistent",
                    "Spend/MCap", "Market Cap", "Sector",
                ]
                st.dataframe(
                    disp[display_cols],
                    use_container_width=True,
                    hide_index=True,
                    height=620,
                )
                if len(filtered_df) > table_limit:
                    st.caption(
                        f"Showing top {table_limit:,} rows. Download CSV for the full universe."
                    )

                # ── Export with enrichment ──────────────────────────────────────
                export_df = work_df[[
                    "company_name", "ticker", "total_lobbying_spend",
                    "yoy_pct", "qoq_pct", "accel_vs_sector",
                    "is_spike", "is_consistent",
                    "market_cap", "spend_to_mcap_ratio",
                    "sector", "quarters_covered",
                ]].copy()
                export_df["ticker"] = export_df["ticker"].fillna("")
                st.download_button(
                    label="Download Leaderboard CSV",
                    data=export_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"leaderboard_{selected_year}_{selected_quarter}.csv",
                    mime="text/csv",
                    key="dl_tab2_enriched",
                )

                # ── Lobbying Yield sub-table ────────────────────────────────────
                st.markdown("---")
                st.markdown("#### Top 50 by Spend / Market Cap % (Lobbying Yield)")
                ratio_df = filtered_df[
                    filtered_df["spend_to_mcap_ratio"].notna()
                    & filtered_df["market_cap"].notna()
                ].copy()
                if not ratio_df.empty:
                    ratio_df = ratio_df.sort_values("spend_to_mcap_ratio", ascending=False).head(50)
                    ratio_df["ticker"] = ratio_df["ticker"].fillna("N/A")
                    ratio_df["Rank"]        = range(1, len(ratio_df) + 1)
                    ratio_df["Company"]     = ratio_df["company_name"]
                    ratio_df["Ticker"]      = ratio_df["ticker"]
                    ratio_df["Total Spend"] = ratio_df["total_lobbying_spend"].apply(format_currency)
                    ratio_df["Market Cap"]  = ratio_df["market_cap"].apply(format_currency)
                    ratio_df["Spend/MCap %"] = ratio_df["spend_to_mcap_ratio"].apply(
                        lambda x: f"{x*100:.4f}%"
                    )
                    ratio_df["YoY %"]       = ratio_df["yoy_pct"].apply(_fmt_pct) if "yoy_pct" in ratio_df else "N/A"
                    ratio_df["Sector"]      = ratio_df["sector"].fillna("N/A")
                    st.dataframe(
                        ratio_df[["Rank", "Company", "Ticker", "Total Spend",
                                  "Market Cap", "Spend/MCap %", "YoY %", "Sector"]],
                        use_container_width=True,
                        hide_index=True,
                        height=400,
                    )
                else:
                    st.info("No companies with market cap data available for this view.")

        with col2:
            st.markdown("#### Summary")
            st.metric("Companies", f"{len(filtered_df):,}")
            if not filtered_df.empty:
                total_spend = filtered_df["total_lobbying_spend"].sum()
                avg_spend   = filtered_df["total_lobbying_spend"].mean()
                st.metric("Total Spend", format_currency(total_spend))
                st.metric("Avg per Company", format_currency(avg_spend))
                if "is_spike" in filtered_df.columns:
                    n_spike = int(filtered_df["is_spike"].sum())
                    n_cons  = int(filtered_df["is_consistent"].sum())
                    st.metric("Spike Flags", f"{n_spike:,}")
                    st.metric("Consistent Ramp", f"{n_cons:,}")
            else:
                st.metric("Total Spend", "$0")
                st.metric("Avg per Company", "$0")

            st.markdown("---")
            st.markdown("**Sector Breakdown**")
            if not filtered_df.empty:
                sec_data = (
                    filtered_df.groupby("sector")["total_lobbying_spend"]
                    .sum()
                    .reset_index()
                    .nlargest(5, "total_lobbying_spend")
                )
                sec_data.columns = ["Sector", "Spend"]
                fig_pie = go.Figure(data=[go.Pie(
                    labels=sec_data["Sector"],
                    values=sec_data["Spend"],
                    hole=0.4,
                    marker=dict(colors=["#60a5fa", "#34d399", "#fbbf24", "#f87171", "#a78bfa"]),
                )])
                fig_pie.update_layout(
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=11),
                    height=250, showlegend=True,
                    margin=dict(l=0, r=0, t=20, b=0),
                )
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.info("No data for sector breakdown.")

            st.markdown("---")
            st.markdown("**Top YoY Growth**")
            growth_df = get_yoy_growth_leaders(
                fetcher.db_path, selected_year,
                refresh_token=st.session_state["cache_buster"],
            )
            if growth_df.empty:
                st.caption(f"Need both {selected_year - 1} and {selected_year} in DB.")
            else:
                for _, row in growth_df.iterrows():
                    label = (
                        row["ticker"]
                        if pd.notna(row["ticker"]) and str(row["ticker"]).strip()
                        else row["company_name"][:20]
                    )
                    sign  = "+" if row["yoy_pct"] >= 0 else ""
                    color = "#22c55e" if row["yoy_pct"] >= 0 else "#ef4444"
                    st.markdown(
                        f"**{label}**: <span style='color:{color}'>{sign}{row['yoy_pct']:.1f}%</span>",
                        unsafe_allow_html=True,
                    )

        # ── Company Detail Drill-down ──────────────────────────────────────────
        st.markdown("---")
        with st.expander("Company / Ticker Detail", expanded=False):
            st.caption(
                "Enter a ticker or company name to see the quarterly spend history "
                "and sector comparison for that entity."
            )
            drill_input = st.text_input(
                "Ticker or Company Name",
                placeholder="e.g. AMZN or Amazon",
                key="tab2_drill_input",
            ).strip().upper()

            if drill_input:
                import sqlite3 as _sq3
                _dc = _sq3.connect(fetcher.db_path)
                try:
                    _dq = pd.read_sql_query(
                        """
                        SELECT year, quarter, company_name, ticker,
                               SUM(total_lobbying_spend) AS q_spend
                        FROM company_lobbying
                        WHERE UPPER(ticker) = ?
                           OR UPPER(company_name) LIKE ?
                        GROUP BY year, quarter, company_name, ticker
                        ORDER BY year, quarter
                        """,
                        _dc,
                        params=(drill_input, f"%{drill_input}%"),
                    )
                    _sp_q = pd.read_sql_query(
                        """
                        SELECT date, return_1m, return_3m, return_6m, return_1y
                        FROM stock_performance
                        WHERE UPPER(ticker) = ?
                        ORDER BY date
                        """,
                        _dc,
                        params=(drill_input,),
                    )
                finally:
                    _dc.close()

                if _dq.empty:
                    st.warning(f"No data found for '{drill_input}'. Check spelling or try the full company name.")
                else:
                    co_name = _dq["company_name"].iloc[0]
                    co_tick = (
                        _dq["ticker"].dropna().iloc[0]
                        if _dq["ticker"].notna().any()
                        else "N/A"
                    )
                    st.markdown(f"**{co_name}** ({co_tick})")

                    # Quarter-label for x-axis
                    _dq["period"] = _dq["year"].astype(str) + " " + _dq["quarter"]

                    fig_drill = go.Figure()
                    fig_drill.add_trace(go.Bar(
                        x=_dq["period"],
                        y=_dq["q_spend"] / 1_000_000,
                        name="Lobbying Spend ($M)",
                        marker_color="#60a5fa",
                        text=(_dq["q_spend"] / 1_000_000).apply(lambda v: f"${v:.2f}M"),
                        textposition="outside",
                    ))
                    fig_drill.update_layout(
                        xaxis_title="Quarter",
                        yaxis_title="Spend ($M)",
                        plot_bgcolor="#0e1117",
                        paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        xaxis=dict(gridcolor="#2d3748"),
                        yaxis=dict(gridcolor="#2d3748"),
                        height=380,
                        margin=dict(l=0, r=0, t=10, b=0),
                    )
                    st.plotly_chart(fig_drill, use_container_width=True)

                    # Sector comparison panel
                    if not filtered_df.empty and "sector" in _dq.columns:
                        co_sector = (
                            filtered_df.loc[
                                filtered_df["company_name"] == co_name, "sector"
                            ].iloc[0]
                            if co_name in filtered_df["company_name"].values
                            else None
                        )
                        if co_sector and pd.notna(co_sector):
                            sec_peers = filtered_df[
                                filtered_df["sector"] == co_sector
                            ]["total_lobbying_spend"]
                            co_spend = _dq[_dq["year"] == selected_year]["q_spend"].sum()
                            pct_rank = (sec_peers <= co_spend).mean() * 100
                            sec_med  = sec_peers.median()
                            st.markdown(f"**Sector:** {co_sector}")
                            sc1, sc2, sc3 = st.columns(3)
                            sc1.metric("Sector Spend Rank", f"{pct_rank:.0f}th pct.")
                            sc2.metric("Sector Median Spend", format_currency(sec_med))
                            sc3.metric(f"{selected_year} Spend", format_currency(co_spend))

                    # Stock return snapshot if available
                    if not _sp_q.empty:
                        st.markdown("**Stock Performance Snapshots**")
                        _sp_q["date"] = pd.to_datetime(_sp_q["date"]).dt.date
                        st.dataframe(
                            _sp_q.rename(columns={
                                "date": "Date",
                                "return_1m": "1M Return (%)",
                                "return_3m": "3M Return (%)",
                                "return_6m": "6M Return (%)",
                                "return_1y": "1Y Return (%)",
                            }),
                            use_container_width=True,
                            hide_index=True,
                        )

        # ── Full-width: Treemap ────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("#### Lobbying Spend Treemap by Sector")
        st.caption(
            "Relative block size = total lobbying spend. "
            "Click a sector block to zoom in on individual companies."
        )
        _tm_df = filtered_df[
            filtered_df["sector"].notna() & (filtered_df["total_lobbying_spend"] > 0)
        ]
        if _tm_df.empty:
            st.info(
                "No sector data available for treemap. "
                "Run a sector backfill first (Advanced: Enrich Existing Data → Refresh Sectors)."
            )
        else:
            st.plotly_chart(
                create_lobbying_treemap(_tm_df, selected_year),
                use_container_width=True,
            )

        # ── OpenSecrets Cross-Reference ────────────────────────────────────────
        with st.expander("OpenSecrets Cross-Reference (Political Contributions)", expanded=False):
            st.caption(
                "PAC contributions and political spending data from OpenSecrets, "
                "synced via Settings → OpenSecrets API. Requires an API key."
            )
            _os_data = _load_opensecrets_contribs(
                fetcher.db_path, selected_year, st.session_state["cache_buster"]
            )
            if _os_data.empty:
                st.info(
                    "No OpenSecrets data yet for this year. "
                    "Go to **Settings → OpenSecrets API** to add your API key and run a sync."
                )
            else:
                st.dataframe(
                    _os_data.rename(columns={
                        "company_name":  "Company",
                        "ticker":        "Ticker",
                        "total_contribs":"Total Contribs ($)",
                        "pacs":          "PAC ($)",
                        "indivs":        "Individual ($)",
                        "os_lobbying":   "OS Lobbying ($)",
                        "outside_spend": "Outside Spend ($)",
                    }),
                    use_container_width=True,
                    hide_index=True,
                    height=350,
                )
                st.caption(
                    f"Showing {len(_os_data)} companies. "
                    "Contributions are for the full election cycle, not calendar year."
                )

    with tab3:
        st.markdown("### Strategy Analytics")
        st.info(
            "**Start here:** Use **Production Picks** as the primary decision list. "
            "All strategy performance panels below are aligned to that production model."
        )

        # ── Stock performance coverage banner ─────────────────────────────────
        import sqlite3 as _sp_sq3
        _sp_conn = _sp_sq3.connect(fetcher.db_path)
        try:
            _sp_years = pd.read_sql_query(
                """SELECT CAST(strftime('%Y', date) AS INTEGER) AS yr,
                          COUNT(DISTINCT ticker) as n
                   FROM stock_performance GROUP BY yr ORDER BY yr""",
                _sp_conn,
            )
        except Exception:
            _sp_years = pd.DataFrame()
        finally:
            _sp_conn.close()

        _all_lobby_years = sorted([
            y for y in get_available_years(
                fetcher.db_path, st.session_state["cache_buster"]
            ) if y <= (datetime.now().year - 1)
        ])
        _sp_covered = _sp_years["yr"].tolist() if not _sp_years.empty else []
        _sp_missing = [y for y in _all_lobby_years if y not in _sp_covered]

        if _sp_missing:
            st.info(
                f"Stock price data is not yet populated for "
                f"**{', '.join(str(y) for y in _sp_missing)}**. "
                "Return metrics for those years will show as N/A until "
                "**Populate Stock Performance** is run in Settings "
                f"(set lookback to {len(_all_lobby_years)} years). "
                "Lobbying data for all years is fully available."
            )
        else:
            st.caption(
                f"Stock performance populated for all years: "
                f"{', '.join(str(y) for y in _sp_covered)}."
            )

        # ── Section 1: Production Picks (Next Hold Year) ─────────────────────
        prod_primary_factor_cfg = str(
            getattr(config, "PRODUCTION_PRIMARY_FACTOR", "hist_spend_z")
        )
        prod_fallback_factor_cfg = str(
            getattr(config, "PRODUCTION_FALLBACK_FACTOR", "accel_score")
        )
        prod_top_n_cfg = int(getattr(config, "PRODUCTION_TOP_N", 10))
        prod_min_usable_cfg = int(getattr(config, "PRODUCTION_MIN_USABLE_NAMES", 5))
        prod_signal_year = int(selected_year)
        prod_hold_year_next = int(selected_year) + 1

        st.markdown(
            f"#### Production Picks — Signal {prod_signal_year} -> Hold {prod_hold_year_next}"
        )
        st.caption(
            "Primary model ranks public companies by **hist_spend_z** "
            "(lobbying spend spike vs own history). "
            "Fallback model uses **composite acceleration score** if primary coverage is thin."
        )
        st.caption(
            "Interpretation: names below are the current model watchlist generated from "
            f"{prod_signal_year} lobbying filings for trading into {prod_hold_year_next}."
        )

        prod_next_df, prod_next_meta = get_production_strategy_holdings(
            fetcher.db_path,
            hold_year=prod_hold_year_next,
            top_n=prod_top_n_cfg,
            primary_factor=prod_primary_factor_cfg,
            fallback_factor=prod_fallback_factor_cfg,
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
            min_usable_names=prod_min_usable_cfg,
        )

        if prod_next_df.empty:
            st.info(
                f"No production picks available for signal year {prod_signal_year} "
                f"(hold year {prod_hold_year_next})."
            )
        else:
            pp1, pp2, pp3, pp4 = st.columns(4)
            pp1.metric("Signal Year", f"{prod_signal_year}")
            pp2.metric("Target Hold Year", f"{prod_hold_year_next}")
            pp3.metric("Model Used", str(prod_next_meta.get("model_used", "N/A")))
            pp4.metric("Names", f"{int(prod_next_meta.get('selected_n', len(prod_next_df)))}")

            if prod_next_meta.get("reason"):
                st.caption(str(prod_next_meta.get("reason")))

            prod_list = prod_next_df.copy().reset_index(drop=True)
            prod_list.insert(0, "Rank", prod_list.index + 1)
            prod_list["annual_spend"] = prod_list["annual_spend"].apply(format_currency)
            for c in ["hist_spend_z", "accel_score"]:
                if c in prod_list.columns:
                    prod_list[c] = prod_list[c].apply(
                        lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                    )
            prod_list = prod_list.rename(
                columns={
                    "ticker": "Ticker",
                    "company_name": "Company",
                    "sector": "Sector",
                    "annual_spend": "Annual Spend",
                    "hist_spend_z": "Hist Z",
                    "accel_score": "Composite",
                }
            )
            st.dataframe(
                prod_list[
                    [
                        c
                        for c in [
                            "Rank",
                            "Ticker",
                            "Company",
                            "Sector",
                            "Annual Spend",
                            "Hist Z",
                            "Composite",
                        ]
                        if c in prod_list.columns
                    ]
                ],
                use_container_width=True,
                hide_index=True,
                height=360,
            )

        st.markdown("---")

        # ── Section 2: Signal Returns ──────────────────────────────────────────
        st.markdown("#### Signal Validation — Forward Returns")

        # ── How returns are measured (collapsible explainer) ──────────────────
        _hold_year = int(selected_year)
        _signal_year = _hold_year - 1
        _today = datetime.now().replace(tzinfo=None)
        _lag_days = int(getattr(config, "LDA_FILING_LAG_DAYS", 20) or 0)
        _ref_date = pd.to_datetime(
            _q4_signal_window(_signal_year, window_days=46)[0]
        ).to_pydatetime()
        _period_meta = {
            "return_1m": ("1-Month",  _ref_date + timedelta(days=30)),
            "return_3m": ("3-Month",  _ref_date + timedelta(days=91)),
            "return_6m": ("6-Month",  _ref_date + timedelta(days=182)),
            "return_1y": ("1-Year",   _ref_date + timedelta(days=365)),
        }

        with st.expander("How are forward returns calculated?", expanded=False):
            st.markdown(
                f"Selected year is treated as the **hold year** (`{_hold_year}`). "
                f"Signals come from prior-year Q4 filings (`{_signal_year}`), anchored to "
                f"**{_ref_date.strftime('%b %d, %Y')}** "
                f"(`{_signal_year}` Q4 + {_lag_days} day lag). "
                f"Each window then looks forward:\n\n"
                + "\n".join(
                    f"- **{lbl}**: measures stock price change from "
                    f"{_ref_date.strftime('%b %d, %Y')} → "
                    f"{tgt.strftime('%b %d, %Y')} "
                    f"({'✅ past' if tgt <= _today else '⏳ future'})"
                    for _, (lbl, tgt) in _period_meta.items()
                )
            )

        signal_df = get_signal_returns(
            fetcher.db_path,
            _signal_year,
            refresh_token=st.session_state["cache_buster"],
        )

        if signal_df.empty:
            st.info(
                f"No return data found for hold year `{_hold_year}` "
                f"(signal year `{_signal_year}`). "
                "Run **Populate Stock Performance** in the sidebar "
                "(Advanced: Enrich Existing Data) to unlock this section."
            )
        else:
            # ── Per-period staleness analysis ─────────────────────────────────
            # A period is "stale" if its target date is in the past but the DB
            # has fewer than 20% of companies with a return value — meaning
            # populate_stock_performance was run before that date and never re-run.
            _n_total = len(signal_df)
            _period_status: dict[str, tuple] = {}
            _stale_labels: list[str] = []

            for _col, (_lbl, _tgt) in _period_meta.items():
                _n_avail = int(signal_df[_col].notna().sum())
                if _tgt > _today:
                    _period_status[_col] = ("future", _tgt.strftime("%b %Y"),
                                            _n_avail, _n_total)
                elif _n_avail < max(1, _n_total * 0.2):
                    _period_status[_col] = ("stale", _tgt.strftime("%b %Y"),
                                            _n_avail, _n_total)
                    _stale_labels.append(_lbl)
                else:
                    _period_status[_col] = ("ok", None, _n_avail, _n_total)

            _future_periods = [
                (_lbl, _tgt.strftime("%b %d, %Y"), _period_status[_col][2])
                for _col, (_lbl, _tgt) in _period_meta.items()
                if _period_status[_col][0] == "future"
            ]

            # ── Staleness banner + inline Refresh button ──────────────────────
            if _stale_labels:
                _ban_c1, _ban_c2 = st.columns([4, 1])
                with _ban_c1:
                    st.warning(
                        f"**Stock data stale for hold year {_hold_year}.** "
                        f"The **{', '.join(_stale_labels)}** window"
                        f"{'s have' if len(_stale_labels) > 1 else ' has'} already passed "
                        f"but return data was never computed — `populate_stock_performance` "
                        f"was last run before those target dates. "
                        f"Click **Refresh** to fill in the missing returns now."
                    )
                with _ban_c2:
                    if st.button(
                        "Refresh Now",
                        key="signal_refresh_sp",
                        use_container_width=True,
                        type="primary",
                    ):
                        with st.spinner("Fetching price history from Yahoo Finance…"):
                            try:
                                from build_company_lobbying import populate_stock_performance
                                _r = populate_stock_performance(
                                    db_path=fetcher.db_path, lookback_years=7
                                )
                                st.success(
                                    f"Done: {_r['tickers_processed']} tickers, "
                                    f"{_r['rows_inserted']} rows updated."
                                )
                                st.session_state["cache_buster"] += 1
                                st.rerun()
                            except Exception as _e:
                                st.error(f"Refresh failed: {_e}")

            if _future_periods:
                _pending_txt = ", ".join(
                    f"{_lbl} (target: {_dt}; available now: {_n_avail}/{_n_total})"
                    for _lbl, _dt, _n_avail in _future_periods
                )
                st.info(
                    f"Forward windows still pending for hold year `{_hold_year}` "
                    f"(signal year `{_signal_year}`): {_pending_txt}. "
                    "This is expected for the latest hold year. Re-run "
                    "**Populate Stock Performance** after each target date passes."
                )

            # ── KPI metrics — smart display per period ────────────────────────
            sr1, sr2, sr3, sr4 = st.columns(4)
            for _display_col, _col, _base_label in [
                (sr1, "return_1m", "1-Month"),
                (sr2, "return_3m", "3-Month"),
                (sr3, "return_6m", "6-Month"),
                (sr4, "return_1y", "1-Year"),
            ]:
                _val = signal_df[_col].dropna().mean()
                _status, _tgt_str, _n_avail, _ = _period_status[_col]

                if pd.notna(_val):
                    _display_col.metric(
                        f"Avg {_base_label}",
                        f"{_val:+.1f}%",
                        f"{_n_avail}/{_n_total} companies",
                    )
                elif _status == "future":
                    _display_col.metric(
                        f"Avg {_base_label}",
                        f"~{_tgt_str}",
                        "Not reached yet",
                        delta_color="off",
                    )
                elif _status == "stale":
                    _display_col.metric(
                        f"Avg {_base_label}",
                        "Refresh ↑",
                        f"Target: {_tgt_str}",
                        delta_color="off",
                    )
                else:
                    _display_col.metric(f"Avg {_base_label}", "N/A", delta_color="off")

            st.markdown("---")

            # ── Distribution histogram — use best available period ─────────────
            # Pick longest period that actually has data, falling back gracefully.
            _best_period_col = None
            _best_period_lbl = None
            for _col, (_lbl, _) in reversed(list(_period_meta.items())):
                if signal_df[_col].notna().sum() > 2:
                    _best_period_col = _col
                    _best_period_lbl = _lbl
                    break

            win_col1, win_col2 = st.columns(2)
            with win_col1:
                if _best_period_col:
                    st.markdown(f"**{_best_period_lbl} Return Distribution**")
                    rets = signal_df[_best_period_col].dropna()
                    fig_hist = go.Figure()
                    fig_hist.add_trace(go.Histogram(
                        x=rets, nbinsx=20,
                        marker_color="#60a5fa", opacity=0.85,
                    ))
                    fig_hist.add_vline(
                        x=0, line_color="#ef4444", line_width=2, line_dash="dash"
                    )
                    fig_hist.update_layout(
                        xaxis_title=f"{_best_period_lbl} Return (%)",
                        yaxis_title="Count",
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=11),
                        height=300, margin=dict(l=0, r=0, t=10, b=0),
                    )
                    st.plotly_chart(fig_hist, use_container_width=True)
                else:
                    st.info("No return data available for histogram yet.")

            with win_col2:
                if _best_period_col:
                    rets = signal_df[_best_period_col].dropna()
                    st.markdown(f"**{_best_period_lbl} Return Stats**")
                    win_rate = (rets > 0).mean() * 100
                    st.metric("Win Rate (>0%)", f"{win_rate:.1f}%")
                    st.metric(f"Median {_best_period_lbl}", f"{rets.median():+.1f}%")
                    st.metric("Best", f"{rets.max():+.1f}%")
                    st.metric("Worst", f"{rets.min():+.1f}%")
                    st.metric("Companies with Data", f"{len(rets)}")
                    # 1-year stats if different from best period
                    if _best_period_col != "return_1y":
                        rets_1y = signal_df["return_1y"].dropna()
                        if not rets_1y.empty:
                            st.markdown("---")
                            st.caption("1-Year (where available)")
                            st.metric("Median 1Y Return", f"{rets_1y.median():+.1f}%")

            # ── Company-level table — label N/A cells meaningfully ────────────
            st.markdown("**Company-Level Returns**")
            ret_disp = signal_df.copy()
            ret_disp["ticker"] = ret_disp["ticker"].fillna("N/A")
            ret_disp["Signal Date"] = (
                pd.to_datetime(ret_disp["signal_date"], errors="coerce")
                .dt.strftime("%Y-%m-%d")
                .fillna("N/A")
            )
            ret_disp["Total Spend"] = ret_disp["total_spend"].apply(format_currency)
            for _col, (_lbl, _tgt) in _period_meta.items():
                _tgt_str_short = _tgt.strftime("%b '%y")
                _is_future = _tgt > _today
                ret_disp[_col] = ret_disp[_col].apply(
                    lambda x, _f=_is_future, _ts=_tgt_str_short:
                        f"{x:+.1f}%" if pd.notna(x)
                        else (f"pending (~{_ts})" if _f else "refresh")
                )
            ret_disp = ret_disp[[
                "company_name", "ticker", "sector", "Signal Date", "Total Spend",
                "return_1m", "return_3m", "return_6m", "return_1y",
            ]]
            ret_disp.columns = [
                "Company", "Ticker", "Sector", "Signal Date", f"Q4 Spend ({_signal_year} Signal)",
                "1-Month", "3-Month", "6-Month", "1-Year",
            ]
            st.dataframe(ret_disp, use_container_width=True, hide_index=True, height=400)

        st.markdown("---")

        # ── Section 3: Strategy vs S&P 500 Benchmark ──────────────────────────
        st.markdown("#### Strategy vs. S&P 500 — Annual Returns")
        _render_benchmark_section(
            fetcher.db_path,
            cache_buster=st.session_state["cache_buster"],
            key_prefix="tab3_bench",
        )

        st.markdown("---")

        # ── Section 4: Production Go/No-Go Truth Panel ──────────────────────
        st.markdown("#### Production Go / No-Go Truth Panel")
        st.caption(
            "Hard deployment gates on the locked production model: "
            "net alpha, outlier robustness, sector-neutral robustness, and statistical confidence."
        )

        prod_primary_factor = str(
            getattr(config, "PRODUCTION_PRIMARY_FACTOR", "hist_spend_z")
        )
        prod_fallback_factor = str(
            getattr(config, "PRODUCTION_FALLBACK_FACTOR", "accel_score")
        )
        prod_top_n = int(getattr(config, "PRODUCTION_TOP_N", 10))
        prod_min_usable = int(getattr(config, "PRODUCTION_MIN_USABLE_NAMES", 5))
        sector_k = int(getattr(config, "SECTOR_NEUTRAL_TOP_K_DEFAULT", 1))

        go_summary, go_yearly = get_production_go_nogo_metrics(
            fetcher.db_path,
            top_n=prod_top_n,
            primary_factor=prod_primary_factor,
            fallback_factor=prod_fallback_factor,
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
            min_usable_names=prod_min_usable,
            sector_k=sector_k,
        )

        if not go_summary.get("ready"):
            st.info(f"Go/No-Go panel unavailable: {go_summary.get('reason', 'No data.')}")
        else:
            if go_summary.get("spx_error"):
                st.warning(f"S&P benchmark note: {go_summary['spx_error']}")

            def _pf(flag: bool) -> str:
                return "PASS" if bool(flag) else "FAIL"

            def _pct(v, signed=True):
                if v is None or pd.isna(v):
                    return "N/A"
                return f"{v:+.2f}%" if signed else f"{v:.1f}%"

            g1, g2, g3, g4, g5 = st.columns(5)
            g1.metric(
                "Net Alpha Gate",
                _pf(go_summary.get("pass_net")),
                (
                    f"{_pct(go_summary.get('avg_alpha_net'))}, "
                    f"{_pct(go_summary.get('win_rate_net'), signed=False)} wins"
                ),
            )
            g2.metric(
                "Robustness Gate",
                _pf(go_summary.get("pass_robust")),
                (
                    f"{_pct(go_summary.get('avg_alpha_ex_best'))}, "
                    f"{_pct(go_summary.get('win_rate_ex_best'), signed=False)} wins"
                ),
            )
            g3.metric(
                "Sector-Neutral Gate",
                _pf(go_summary.get("pass_sector")),
                (
                    f"{_pct(go_summary.get('avg_sector_neutral_alpha'))}, "
                    f"{_pct(go_summary.get('win_rate_sector_neutral'), signed=False)} wins"
                ),
            )
            g4.metric(
                "Significance Gate",
                _pf(go_summary.get("pass_significance")),
                (
                    f"CI [{go_summary.get('ci_low', float('nan')):+.2f}%, "
                    f"{go_summary.get('ci_high', float('nan')):+.2f}%], "
                    f"p={go_summary.get('p_binom', float('nan')):.3f}"
                ),
            )
            g5.metric("Decision", "GO" if go_summary.get("go_live") else "NO-GO")

            if go_summary.get("go_live"):
                st.success(
                    "Production model passes all deployment gates under current thresholds."
                )
            else:
                st.error(
                    "Production model fails one or more deployment gates. "
                    "Treat as research-only until gates pass."
                )

            st.caption(
                "Thresholds: "
                f"net alpha >= {go_summary.get('threshold_net_alpha', 0):.2f}%, "
                f"net win rate >= {go_summary.get('threshold_win_rate', 0):.1f}%, "
                f"robust alpha >= {go_summary.get('threshold_robust_alpha', 0):.2f}%, "
                f"sector-neutral alpha >= {go_summary.get('threshold_sector_alpha', 0):.2f}%, "
                f"binomial p <= {go_summary.get('threshold_p', 0):.2f}, "
                "and bootstrap CI lower bound > 0."
            )

            with st.expander("Go/No-Go Annual Diagnostics", expanded=False):
                show_go = go_yearly.copy()
                for c in [
                    "top_return_1y",
                    "spx_return",
                    "alpha_vs_spx_1y",
                    "net_alpha_vs_spx_1y",
                    "alpha_ex_best_1y",
                    "turnover_pct",
                    "cost_pct",
                ]:
                    if c in show_go.columns:
                        if c in {"turnover_pct", "cost_pct"}:
                            show_go[c] = show_go[c].apply(
                                lambda v: f"{v:.2f}%" if pd.notna(v) else "N/A"
                            )
                        else:
                            show_go[c] = show_go[c].apply(
                                lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                            )
                show_go = show_go.rename(
                    columns={
                        "signal_year": "Signal Year",
                        "year": "Hold Year",
                        "model_used": "Model",
                        "fallback_used": "Fallback",
                        "top_return_1y": "Top 1Y",
                        "spx_return": "SPX 1Y",
                        "alpha_vs_spx_1y": "Raw Alpha",
                        "net_alpha_vs_spx_1y": "Net Alpha",
                        "alpha_ex_best_1y": "Alpha ex-Best",
                        "turnover_pct": "Turnover",
                        "cost_pct": "Cost Drag",
                    }
                )
                st.dataframe(show_go, use_container_width=True, hide_index=True)

        st.markdown("---")

        # ── Section 5: Production Strategy + Factor Lab ──────────────────────
        st.markdown("#### Production Strategy (Primary + Fallback)")
        prod_hold_year = int(selected_year) + 1

        st.caption(
            f"Default production model uses **{prod_primary_factor}** (Top {prod_top_n}) "
            f"with fallback to **{prod_fallback_factor}** when primary coverage is thin."
        )
        st.caption(
            f"Year control is interpreted as signal year here: "
            f"{int(selected_year)} filings -> hold year {prod_hold_year}."
        )

        prod_live_df, prod_live_meta = get_production_strategy_holdings(
            fetcher.db_path,
            hold_year=prod_hold_year,
            top_n=prod_top_n,
            primary_factor=prod_primary_factor,
            fallback_factor=prod_fallback_factor,
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
            min_usable_names=prod_min_usable,
        )

        if prod_live_df.empty:
            st.info(
                f"No production holdings for hold year `{prod_hold_year}` "
                f"(signal year `{prod_hold_year - 1}`). "
                "Check ticker coverage and return data freshness."
            )
        else:
            pr1, pr2, pr3, pr4 = st.columns(4)
            with pr1:
                st.metric("Hold Year", f"{prod_hold_year}")
            with pr2:
                st.metric("Signal Year", f"{int(prod_live_meta.get('signal_year', prod_hold_year - 1))}")
            with pr3:
                st.metric("Model Used", str(prod_live_meta.get("model_used", "N/A")))
            with pr4:
                st.metric("Selected Names", f"{int(prod_live_meta.get('selected_n', len(prod_live_df)))}")

            if prod_live_meta.get("reason"):
                st.caption(str(prod_live_meta.get("reason")))

            prod_show = prod_live_df.copy()
            prod_show["annual_spend"] = prod_show["annual_spend"].apply(format_currency)
            for c in ["hist_spend_z", "accel_score"]:
                if c in prod_show.columns:
                    prod_show[c] = prod_show[c].apply(
                        lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                    )
            for c in ["return_1m", "return_3m", "return_6m", "return_1y"]:
                if c in prod_show.columns:
                    prod_show[c] = prod_show[c].apply(
                        lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                    )
            if "signal_date" in prod_show.columns:
                prod_show["signal_date"] = (
                    pd.to_datetime(prod_show["signal_date"], errors="coerce")
                    .dt.strftime("%Y-%m-%d")
                    .fillna("N/A")
                )
            prod_show = prod_show.rename(
                columns={
                    "company_name": "Company",
                    "ticker": "Ticker",
                    "sector": "Sector",
                    "signal_date": "Signal Date",
                    "annual_spend": "Annual Spend",
                    "hist_spend_z": "Hist Z",
                    "accel_score": "Composite",
                    "return_1m": "Fwd 1M",
                    "return_3m": "Fwd 3M",
                    "return_6m": "Fwd 6M",
                    "return_1y": "Fwd 1Y",
                }
            )
            keep_cols = [
                c
                for c in [
                    "Company",
                    "Ticker",
                    "Sector",
                    "Signal Date",
                    "Annual Spend",
                    "Hist Z",
                    "Composite",
                    "Fwd 1M",
                    "Fwd 3M",
                    "Fwd 6M",
                    "Fwd 1Y",
                ]
                if c in prod_show.columns
            ]
            st.dataframe(
                prod_show[keep_cols],
                use_container_width=True,
                hide_index=True,
                height=300,
            )

        prod_bt_df, prod_bt_detail_df = get_production_strategy_backtest(
            fetcher.db_path,
            top_n=prod_top_n,
            primary_factor=prod_primary_factor,
            fallback_factor=prod_fallback_factor,
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
            min_usable_names=prod_min_usable,
        )
        if not prod_bt_df.empty:
            prod_eval = prod_bt_df.dropna(subset=["alpha_vs_spx_1y"]).copy()
            pm1, pm2, pm3, pm4 = st.columns(4)
            pm1.metric("Backtest Years", f"{len(prod_eval):,}")
            pm2.metric(
                "Avg Alpha vs SPX (1Y)",
                (
                    f"{prod_eval['alpha_vs_spx_1y'].mean():+.2f}%"
                    if not prod_eval.empty
                    else "N/A"
                ),
            )
            pm3.metric(
                "Win Rate vs SPX",
                (
                    f"{(prod_eval['alpha_vs_spx_1y'] > 0).mean() * 100.0:.1f}%"
                    if not prod_eval.empty
                    else "N/A"
                ),
            )
            pm4.metric(
                "Fallback Years",
                f"{int(prod_bt_df['fallback_used'].fillna(False).sum())}",
            )

            fig_prod = go.Figure()
            fig_prod.add_trace(
                go.Bar(
                    x=prod_bt_df["year"],
                    y=prod_bt_df["alpha_vs_spx_1y"],
                    marker_color=[
                        "#f59e0b" if bool(v) else "#34d399"
                        for v in prod_bt_df["fallback_used"].fillna(False).tolist()
                    ],
                    text=[
                        f"{v:+.1f}%" if pd.notna(v) else "N/A"
                        for v in prod_bt_df["alpha_vs_spx_1y"]
                    ],
                    textposition="outside",
                    name="Alpha vs SPX (1Y)",
                )
            )
            fig_prod.add_hline(y=0, line_color="#6b7280", line_width=1)
            fig_prod.update_layout(
                xaxis=dict(title="Hold Year", dtick=1, gridcolor="#2d3748"),
                yaxis=dict(title="Alpha vs SPX 1Y (%)", gridcolor="#2d3748"),
                plot_bgcolor="#0e1117",
                paper_bgcolor="#0e1117",
                font=dict(color="#ffffff", size=12),
                height=320,
                margin=dict(l=0, r=20, t=20, b=0),
                showlegend=False,
            )
            st.plotly_chart(fig_prod, use_container_width=True, key="prod_alpha_vs_spx_chart")
            st.caption(
                "Bar color: green = primary model, amber = fallback model."
            )

            with st.expander("Production Strategy Annual Table", expanded=False):
                show_prod_bt = prod_bt_df.copy()
                for c in [
                    "top_return_1m",
                    "top_return_3m",
                    "top_return_6m",
                    "top_return_1y",
                    "universe_return_1y",
                    "alpha_return_1y",
                    "spx_return",
                    "alpha_vs_spx_1y",
                    "return_coverage_pct",
                ]:
                    if c in show_prod_bt.columns:
                        show_prod_bt[c] = show_prod_bt[c].apply(
                            lambda v: f"{v:+.2f}%" if pd.notna(v) and c != "return_coverage_pct"
                            else (f"{v:.1f}%" if pd.notna(v) else "N/A")
                        )
                show_prod_bt = show_prod_bt.rename(
                    columns={
                        "signal_year": "Signal Year",
                        "year": "Hold Year",
                        "model_used": "Model",
                        "fallback_used": "Fallback Used",
                        "n_universe": "Universe N",
                        "n_selected": "Selected N",
                        "top_return_1y": "Top 1Y",
                        "universe_return_1y": "Univ 1Y",
                        "alpha_return_1y": "Alpha vs Univ 1Y",
                        "spx_return": "SPX",
                        "alpha_vs_spx_1y": "Alpha vs SPX 1Y",
                        "return_coverage_pct": "1Y Coverage",
                    }
                )
                st.dataframe(show_prod_bt, use_container_width=True, hide_index=True)

            if not prod_bt_detail_df.empty:
                st.download_button(
                    "Download Production Holdings Backtest CSV",
                    data=prod_bt_detail_df.to_csv(index=False).encode("utf-8"),
                    file_name="production_strategy_holdings.csv",
                    mime="text/csv",
                    key="prod_holdings_dl",
                )

        st.markdown("---")
        st.markdown("#### Acceleration Factor Lab (Research)")
        st.caption(
            "Tests acceleration/spike features rather than raw spend levels: "
            "YoY%, QoQ%, spend z-score vs history, sector-normalized spike, and "
            "YoY change in spend/market-cap ratio."
        )

        acc_c1, acc_c2 = st.columns([2, 1])
        with acc_c1:
            accel_top_n = st.slider(
                "Top N acceleration names (per year)",
                min_value=5,
                max_value=100,
                value=int(getattr(config, "ACCELERATION_TOP_N_DEFAULT", 20)),
                step=5,
                key="accel_top_n",
            )
        with acc_c2:
            accel_sector_k = st.slider(
                "Sector-neutral k/sector",
                min_value=1,
                max_value=3,
                value=int(getattr(config, "SECTOR_NEUTRAL_TOP_K_DEFAULT", 1)),
                step=1,
                key="accel_sector_k",
                help="Long top-k and short bottom-k acceleration names per sector.",
            )

        accel_feat_df = get_acceleration_features(
            fetcher.db_path,
            selected_year,
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
            ticker_only=True,
        )

        if accel_feat_df.empty:
            st.info(
                f"No acceleration feature set available for {selected_year} "
                f"at min spend ${min_spend:.1f}M."
            )
        else:
            accel_ret_df = get_signal_returns(
                fetcher.db_path,
                selected_year,
                refresh_token=st.session_state["cache_buster"],
            )
            if not accel_ret_df.empty:
                accel_now = accel_feat_df.merge(
                    accel_ret_df[["ticker", "return_3m", "return_6m", "return_1y"]],
                    on="ticker",
                    how="left",
                )
            else:
                accel_now = accel_feat_df.copy()
                accel_now["return_3m"] = None
                accel_now["return_6m"] = None
                accel_now["return_1y"] = None

            accel_top_now = accel_now.sort_values(
                ["accel_score", "annual_spend"], ascending=[False, False]
            ).head(int(accel_top_n))

            if not accel_top_now.empty:
                a1, a2, a3, a4 = st.columns(4)
                with a1:
                    st.metric("Scored Universe", f"{len(accel_now):,}")
                with a2:
                    st.metric("Top-N Selected", f"{len(accel_top_now):,}")
                with a3:
                    med_score = accel_now["accel_score"].dropna().median()
                    st.metric("Median Accel Score", f"{med_score:.1f}" if pd.notna(med_score) else "N/A")
                with a4:
                    med_z = accel_now["hist_spend_z"].dropna().median()
                    st.metric("Median Spend Z", f"{med_z:.2f}" if pd.notna(med_z) else "N/A")

                st.markdown("**Top Acceleration Names (Selected Year)**")
                disp_acc = accel_top_now.copy()
                disp_acc["annual_spend"] = disp_acc["annual_spend"].apply(format_currency)
                disp_acc["market_cap"] = disp_acc["market_cap"].apply(format_currency)
                for c in ["yoy_pct", "qoq_pct", "mcap_ratio_yoy_pct", "return_3m", "return_6m", "return_1y"]:
                    disp_acc[c] = disp_acc[c].apply(
                        lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                    )
                for c in ["hist_spend_z", "sector_spike_z", "accel_score"]:
                    disp_acc[c] = disp_acc[c].apply(
                        lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                    )
                disp_acc = disp_acc.rename(
                    columns={
                        "company_name": "Company",
                        "ticker": "Ticker",
                        "sector": "Sector",
                        "annual_spend": "Spend",
                        "market_cap": "Market Cap",
                        "yoy_pct": "YoY%",
                        "qoq_pct": "QoQ%",
                        "hist_spend_z": "Spend Z",
                        "sector_spike_z": "Sector Spike Z",
                        "mcap_ratio_yoy_pct": "d(Spend/MCap) YoY%",
                        "accel_score": "Accel Score",
                        "return_3m": "Fwd 3M",
                        "return_6m": "Fwd 6M",
                        "return_1y": "Fwd 1Y",
                    }
                )
                st.dataframe(
                    disp_acc[
                        [
                            "Company",
                            "Ticker",
                            "Sector",
                            "Spend",
                            "Market Cap",
                            "Accel Score",
                            "YoY%",
                            "QoQ%",
                            "Spend Z",
                            "Sector Spike Z",
                            "d(Spend/MCap) YoY%",
                            "Fwd 3M",
                            "Fwd 6M",
                            "Fwd 1Y",
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                    height=360,
                )

        # Multi-year event-window backtest
        accel_bt_df, accel_detail_df = get_acceleration_event_backtest(
            fetcher.db_path,
            top_n=int(accel_top_n),
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
        )

        if not accel_bt_df.empty:
            st.markdown("**Event-Window Backtest (Top-N Acceleration vs Universe)**")

            kb1, kb2, kb3, kb4 = st.columns(4)
            kb1.metric("Years", f"{len(accel_bt_df)}")
            kb2.metric(
                "Avg Alpha 3M",
                (
                    f"{accel_bt_df['alpha_return_3m'].dropna().mean():+.2f}%"
                    if accel_bt_df["alpha_return_3m"].notna().any()
                    else "N/A"
                ),
            )
            kb3.metric(
                "Avg Alpha 6M",
                (
                    f"{accel_bt_df['alpha_return_6m'].dropna().mean():+.2f}%"
                    if accel_bt_df["alpha_return_6m"].notna().any()
                    else "N/A"
                ),
            )
            kb4.metric(
                "Avg Alpha 1Y",
                (
                    f"{accel_bt_df['alpha_return_1y'].dropna().mean():+.2f}%"
                    if accel_bt_df["alpha_return_1y"].notna().any()
                    else "N/A"
                ),
            )

            fig_acc = go.Figure()
            fig_acc.add_trace(
                go.Scatter(
                    x=accel_bt_df["year"],
                    y=accel_bt_df["alpha_return_3m"],
                    mode="lines+markers",
                    name="Alpha 3M",
                    line=dict(color="#60a5fa", width=2),
                )
            )
            fig_acc.add_trace(
                go.Scatter(
                    x=accel_bt_df["year"],
                    y=accel_bt_df["alpha_return_6m"],
                    mode="lines+markers",
                    name="Alpha 6M",
                    line=dict(color="#34d399", width=2),
                )
            )
            fig_acc.add_trace(
                go.Scatter(
                    x=accel_bt_df["year"],
                    y=accel_bt_df["alpha_return_1y"],
                    mode="lines+markers",
                    name="Alpha 1Y",
                    line=dict(color="#fbbf24", width=2),
                )
            )
            fig_acc.add_hline(y=0, line_color="#6b7280", line_width=1)
            fig_acc.update_layout(
                xaxis=dict(title="Hold Year", dtick=1, gridcolor="#2d3748"),
                yaxis=dict(title="Top-N minus Universe Return (%)", gridcolor="#2d3748"),
                plot_bgcolor="#0e1117",
                paper_bgcolor="#0e1117",
                font=dict(color="#ffffff", size=12),
                legend=dict(
                    bgcolor="#1a1d24",
                    bordercolor="#4a5568",
                    borderwidth=1,
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1,
                ),
                height=360,
                margin=dict(l=0, r=30, t=40, b=0),
            )
            st.plotly_chart(fig_acc, use_container_width=True, key="accel_event_window_chart")

            with st.expander("Acceleration Event Backtest Table", expanded=False):
                show_bt = accel_bt_df.copy()
                pct_cols = [
                    c
                    for c in show_bt.columns
                    if c.startswith("top_return_")
                    or c.startswith("universe_return_")
                    or c.startswith("alpha_return_")
                    or c in {"spx_return", "alpha_vs_spx_1y"}
                ]
                for c in pct_cols:
                    show_bt[c] = show_bt[c].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A")
                show_bt = show_bt.rename(
                    columns={
                        "signal_year": "Signal Year",
                        "year": "Hold Year",
                        "n_universe": "Universe N",
                        "n_top": "Top N",
                        "top_return_3m": "Top 3M",
                        "top_return_6m": "Top 6M",
                        "top_return_1y": "Top 1Y",
                        "universe_return_3m": "Univ 3M",
                        "universe_return_6m": "Univ 6M",
                        "universe_return_1y": "Univ 1Y",
                        "alpha_return_3m": "Alpha 3M",
                        "alpha_return_6m": "Alpha 6M",
                        "alpha_return_1y": "Alpha 1Y",
                        "alpha_vs_spx_1y": "Alpha vs SPX 1Y",
                    }
                )
                st.dataframe(show_bt, use_container_width=True, hide_index=True)

            # Sector-neutral long/short
            sn_df = get_sector_neutral_acceleration_backtest(
                fetcher.db_path,
                top_k_per_sector=int(accel_sector_k),
                refresh_token=st.session_state["cache_buster"],
                min_spend_m=min_spend,
            )
            if not sn_df.empty:
                st.markdown("**Sector-Neutral Long/Short Test**")
                ks1, ks2, ks3 = st.columns(3)
                ks1.metric(
                    "Avg LS 3M",
                    (
                        f"{sn_df['ls_return_3m'].dropna().mean():+.2f}%"
                        if sn_df["ls_return_3m"].notna().any()
                        else "N/A"
                    ),
                )
                ks2.metric(
                    "Avg LS 6M",
                    (
                        f"{sn_df['ls_return_6m'].dropna().mean():+.2f}%"
                        if sn_df["ls_return_6m"].notna().any()
                        else "N/A"
                    ),
                )
                ks3.metric(
                    "Avg LS 1Y",
                    (
                        f"{sn_df['ls_return_1y'].dropna().mean():+.2f}%"
                        if sn_df["ls_return_1y"].notna().any()
                        else "N/A"
                    ),
                )
                with st.expander("Sector-Neutral Annual Table", expanded=False):
                    show_sn = sn_df.copy()
                    for c in [col for col in show_sn.columns if "return_" in col]:
                        show_sn[c] = show_sn[c].apply(
                            lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                        )
                    show_sn = show_sn.rename(
                        columns={
                            "signal_year": "Signal Year",
                            "year": "Hold Year",
                            "n_sectors": "Sectors",
                            "n_long": "Long N",
                            "n_short": "Short N",
                            "ls_return_3m": "LS 3M",
                            "ls_return_6m": "LS 6M",
                            "ls_return_1y": "LS 1Y",
                        }
                    )
                    st.dataframe(show_sn, use_container_width=True, hide_index=True)

            # Regime filter summary
            regime_merged, regime_summary = get_regime_conditioned_acceleration_performance(
                fetcher.db_path,
                top_n=int(accel_top_n),
                refresh_token=st.session_state["cache_buster"],
                min_spend_m=min_spend,
            )
            if regime_summary.empty:
                st.info(
                    "Regime-conditioned summary unavailable (likely due missing macro proxy data)."
                )
            else:
                st.markdown("**Regime-Conditioned Performance**")
                st.caption(
                    "Recession risk uses a proxy (10Y-3M term spread deterioration/inversion), "
                    "not a model-implied probability."
                )
                show_reg = regime_summary.copy()
                for c in [
                    "Avg Top 3M",
                    "Avg Top 6M",
                    "Avg Top 1Y",
                    "Avg Alpha 1Y (vs Univ)",
                    "Avg Alpha 1Y (vs SPX)",
                ]:
                    show_reg[c] = show_reg[c].apply(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                    )
                st.dataframe(show_reg, use_container_width=True, hide_index=True)

            st.markdown("**Robustness Guardrails**")

            # Rolling stability windows
            rolling_df = get_acceleration_rolling_windows(
                fetcher.db_path,
                top_n=int(accel_top_n),
                window_years=3,
                refresh_token=st.session_state["cache_buster"],
                min_spend_m=min_spend,
            )
            if rolling_df.empty:
                st.info("Rolling 3-year stability windows unavailable yet.")
            else:
                st.caption("Rolling 3-year windows (signal years) to test temporal stability.")
                fig_roll = go.Figure()
                fig_roll.add_trace(
                    go.Scatter(
                        x=rolling_df["window_label"],
                        y=rolling_df["avg_alpha_1y"],
                        mode="lines+markers",
                        name="Avg Alpha 1Y",
                        line=dict(color="#60a5fa", width=2),
                    )
                )
                fig_roll.add_hline(y=0, line_color="#6b7280", line_width=1)
                fig_roll.update_layout(
                    xaxis=dict(title="Signal-Year Window", gridcolor="#2d3748"),
                    yaxis=dict(title="Avg 1Y Alpha (%)", gridcolor="#2d3748"),
                    plot_bgcolor="#0e1117",
                    paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    height=280,
                    margin=dict(l=0, r=20, t=20, b=0),
                )
                st.plotly_chart(fig_roll, use_container_width=True, key="accel_roll_stability")
                show_roll = rolling_df.copy()
                for c in ["avg_alpha_3m", "avg_alpha_6m", "avg_alpha_1y", "win_rate_1y"]:
                    show_roll[c] = show_roll[c].apply(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) and c != "win_rate_1y"
                        else (f"{v:.1f}%" if pd.notna(v) else "N/A")
                    )
                show_roll = show_roll.rename(
                    columns={
                        "window_label": "Window",
                        "n_years": "Years",
                        "avg_alpha_3m": "Avg Alpha 3M",
                        "avg_alpha_6m": "Avg Alpha 6M",
                        "avg_alpha_1y": "Avg Alpha 1Y",
                        "win_rate_1y": "1Y Win Rate",
                    }
                )
                st.dataframe(
                    show_roll[["Window", "Years", "Avg Alpha 3M", "Avg Alpha 6M", "Avg Alpha 1Y", "1Y Win Rate"]],
                    use_container_width=True,
                    hide_index=True,
                )

            # Out-of-sample split
            oos_train_start = int(getattr(config, "OOS_TRAIN_START_YEAR", 2019))
            oos_train_end = int(getattr(config, "OOS_TRAIN_END_YEAR", 2022))
            oos_test_start = int(getattr(config, "OOS_TEST_START_YEAR", 2023))
            oos_test_end = int(getattr(config, "OOS_TEST_END_YEAR", 2026))

            oos_annual, oos_summary, oos_weights = get_acceleration_oos_split_backtest(
                fetcher.db_path,
                top_n=int(accel_top_n),
                refresh_token=st.session_state["cache_buster"],
                min_spend_m=min_spend,
                train_start=oos_train_start,
                train_end=oos_train_end,
                test_start=oos_test_start,
                test_end=oos_test_end,
            )
            if oos_annual.empty:
                st.info("Out-of-sample split results unavailable (insufficient train/test return coverage).")
            else:
                st.caption(
                    f"OOS split: train={oos_train_start}-{oos_train_end}, "
                    f"test={oos_test_start}-{oos_test_end}. "
                    "Model weights are fit on train only and then frozen."
                )
                if not oos_summary.empty:
                    show_oos_sum = oos_summary.copy()
                    for c in ["Avg Alpha 3M", "Avg Alpha 6M", "Avg Alpha 1Y", "1Y Win Rate"]:
                        show_oos_sum[c] = show_oos_sum[c].apply(
                            lambda v: f"{v:+.2f}%" if pd.notna(v) and c != "1Y Win Rate"
                            else (f"{v:.1f}%" if pd.notna(v) else "N/A")
                        )
                    st.dataframe(show_oos_sum, use_container_width=True, hide_index=True)
                if not oos_weights.empty:
                    with st.expander("Fitted Train Weights", expanded=False):
                        show_w = oos_weights.copy()
                        show_w["weight"] = show_w["weight"].apply(lambda v: f"{v:+.4f}")
                        st.dataframe(show_w.rename(columns={"feature": "Feature", "weight": "Weight"}),
                                     use_container_width=True, hide_index=True)
                with st.expander("Out-of-Sample Annual Table", expanded=False):
                    show_oos = oos_annual.copy()
                    for c in [col for col in show_oos.columns if "return_" in col or "alpha_" in col]:
                        show_oos[c] = show_oos[c].apply(
                            lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                        )
                    show_oos = show_oos.rename(
                        columns={
                            "signal_year": "Signal Year",
                            "year": "Hold Year",
                            "split": "Split",
                            "n_universe": "Universe N",
                            "n_top": "Top N",
                            "alpha_return_3m": "Alpha 3M",
                            "alpha_return_6m": "Alpha 6M",
                            "alpha_return_1y": "Alpha 1Y",
                        }
                    )
                    st.dataframe(show_oos, use_container_width=True, hide_index=True)

            # Simplicity check
            simp_annual, simp_summary = get_acceleration_simplicity_check(
                fetcher.db_path,
                top_n=int(accel_top_n),
                refresh_token=st.session_state["cache_buster"],
                min_spend_m=min_spend,
                simple_metric="yoy_pct",
            )
            if simp_summary.empty:
                st.info("Simplicity check unavailable (not enough overlap between score and YoY universes).")
            else:
                st.caption(
                    "Simplicity check: composite acceleration score vs single-metric YoY% selection."
                )
                show_simp = simp_summary.copy()
                for c in [
                    "Avg overlap %",
                    "Avg (Simple-Comp) 3M",
                    "Avg (Simple-Comp) 6M",
                    "Avg (Simple-Comp) 1Y",
                    "Tolerance (abs diff 1Y)",
                ]:
                    show_simp[c] = show_simp[c].apply(
                        lambda v: f"{v:.1f}%" if pd.notna(v) and c == "Avg overlap %"
                        else (f"{v:+.2f}%" if pd.notna(v) else "N/A")
                    )
                st.dataframe(show_simp, use_container_width=True, hide_index=True)
                with st.expander("Simplicity Annual Table", expanded=False):
                    show_sa = simp_annual.copy()
                    for c in [col for col in show_sa.columns if "return_" in col or "simple_minus_comp" in col]:
                        show_sa[c] = show_sa[c].apply(
                            lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                        )
                    show_sa = show_sa.rename(
                        columns={
                            "signal_year": "Signal Year",
                            "year": "Hold Year",
                            "overlap_pct": "Overlap %",
                            "simple_minus_comp_return_3m": "Simple-Comp 3M",
                            "simple_minus_comp_return_6m": "Simple-Comp 6M",
                            "simple_minus_comp_return_1y": "Simple-Comp 1Y",
                        }
                    )
                    st.dataframe(show_sa, use_container_width=True, hide_index=True)

            if not accel_detail_df.empty:
                st.download_button(
                    "Download Acceleration Constituents CSV",
                    data=accel_detail_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"acceleration_constituents_{selected_year}.csv",
                    mime="text/csv",
                    key="accel_dl_detail",
                )

        st.markdown("---")

        # ── Section 5: Historical Data Coverage (moved from top) ───────────────
        with st.expander("Historical Data Coverage", expanded=False):
            st.caption("Data completeness metrics stored in your local database.")
            yearly_summary = get_yearly_summary(fetcher.db_path, st.session_state["cache_buster"])

            if yearly_summary.empty:
                st.info("No yearly summary data available yet.")
            else:
                col1, col2 = st.columns(2)

                with col1:
                    st.markdown("**Total Lobbying Spend by Year**")
                    fig = go.Figure()
                    fig.add_trace(go.Bar(
                        x=yearly_summary["year"],
                        y=(yearly_summary["total_lobbying_spend"] / 1_000_000),
                        marker_color="#60a5fa",
                        name="Spend ($M)",
                    ))
                    fig.update_layout(
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        xaxis=dict(gridcolor="#2d3748"),
                        yaxis=dict(gridcolor="#2d3748", title="Spend ($M)"),
                        height=300,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                with col2:
                    st.markdown("**Entity Coverage by Year**")
                    fig = go.Figure()
                    fig.add_trace(go.Bar(
                        x=yearly_summary["year"], y=yearly_summary["unique_entities"],
                        name="Unique Entities", marker_color="#34d399",
                    ))
                    fig.add_trace(go.Bar(
                        x=yearly_summary["year"], y=yearly_summary["mapped_entities"],
                        name="Mapped Tickers", marker_color="#fbbf24",
                    ))
                    fig.update_layout(
                        barmode="group",
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        xaxis=dict(gridcolor="#2d3748"),
                        yaxis=dict(gridcolor="#2d3748", title="Count"),
                        legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568", borderwidth=1),
                        height=300,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                coverage_df = yearly_summary.copy()
                coverage_df["Total Lobbying Spend"] = coverage_df["total_lobbying_spend"].apply(format_currency)
                coverage_df["Ticker Match Rate"] = coverage_df["ticker_match_rate"].apply(lambda x: f"{x:.2f}%")
                coverage_df["Market Cap Coverage"] = coverage_df["market_cap_coverage"].apply(lambda x: f"{x:.2f}%")
                coverage_df["Entity Match Rate"] = coverage_df["entity_match_rate"].apply(lambda x: f"{x:.2f}%")
                coverage_df["Entity MCap Coverage"] = coverage_df["entity_mcap_coverage"].apply(lambda x: f"{x:.2f}%")
                coverage_df = coverage_df[
                    [
                        "year",
                        "unique_entities",
                        "mapped_entities",
                        "with_market_cap_entities",
                        "total_rows",
                        "mapped_rows",
                        "with_market_cap_rows",
                        "Total Lobbying Spend",
                        "Entity Match Rate",
                        "Entity MCap Coverage",
                        "Ticker Match Rate",
                        "Market Cap Coverage",
                    ]
                ]
                coverage_df.columns = [
                    "Year",
                    "Unique Entities",
                    "Mapped Tickers",
                    "Entities With MCap",
                    "Total Rows",
                    "Rows With Ticker",
                    "Rows With MCap",
                    "Total Lobbying Spend",
                    "Entity Match Rate",
                    "Entity MCap Coverage",
                    "Row Match Rate",
                    "Row MCap Coverage",
                ]
                st.dataframe(
                    coverage_df.sort_values("Year", ascending=False),
                    use_container_width=True, hide_index=True, height=250,
                )
    
    # ══════════════════════════════════════════════════════════════════════════
    with tab4:
        st.markdown("### Research Signals")
        st.caption(
            "Three experimental signals derived from lobbying data. "
            "Results improve significantly as more historical years are loaded — "
            "fetch 2019–2022 from the sidebar to extend the backtest window."
        )

        sig_s1, sig_s2, sig_s3 = st.tabs([
            "Sector Rotation", "New Entrant Detection", "Quality Gate (Production)"
        ])

        # ── Signal 1: Sector Rotation ──────────────────────────────────────
        with sig_s1:
            st.markdown("#### Sector Rotation Signal")
            st.caption(
                "Logic: sectors that ramp lobbying spend the most in year Y are "
                "hypothesised to be seeking regulatory tailwinds. "
                "The signal buys the corresponding SPDR sector ETF at the close of "
                "year Y and holds for 12 months (year Y+1 return)."
            )

            with st.spinner("Loading sector rotation data..."):
                sr_df = get_sector_rotation_signal(
                    fetcher.db_path, refresh_token=st.session_state["cache_buster"]
                )
                sector_long_df = get_sector_spend_by_year(
                    fetcher.db_path, refresh_token=st.session_state["cache_buster"]
                )

            if sr_df.empty:
                st.info(
                    "Not enough data to compute sector rotation signal. "
                    "At least two years of lobbying data are required."
                )
            else:
                # ── Sector spend ramp chart (selected year) ────────────────
                if not sector_long_df.empty:
                    st.markdown("**Sector Lobbying Spend Ramp — Selected Year**")
                    yr_sectors = sector_long_df[
                        sector_long_df["year"] == selected_year
                    ].copy()
                    if not yr_sectors.empty and yr_sectors["yoy_pct"].notna().any():
                        yr_sectors = yr_sectors.sort_values("yoy_pct", ascending=True)
                        fig_sr_bar = go.Figure()
                        colors_sr = [
                            "#22c55e" if v >= 0 else "#ef4444"
                            for v in yr_sectors["yoy_pct"]
                        ]
                        fig_sr_bar.add_trace(go.Bar(
                            x=yr_sectors["yoy_pct"],
                            y=yr_sectors["sector"],
                            orientation="h",
                            marker_color=colors_sr,
                            text=[
                                f"{v:+.1f}%" if pd.notna(v) else "N/A"
                                for v in yr_sectors["yoy_pct"]
                            ],
                            textposition="outside",
                            customdata=yr_sectors[["etf", "spend"]].values,
                            hovertemplate=(
                                "<b>%{y}</b><br>"
                                "ETF: %{customdata[0]}<br>"
                                "YoY: %{x:+.1f}%<br>"
                                "Spend: $%{customdata[1]:,.0f}<extra></extra>"
                            ),
                        ))
                        fig_sr_bar.update_layout(
                            title=f"Sector Lobbying YoY% — {selected_year}",
                            xaxis_title="YoY% Change in Lobbying Spend",
                            plot_bgcolor="#0e1117",
                            paper_bgcolor="#0e1117",
                            font=dict(color="#ffffff", size=12),
                            height=max(300, len(yr_sectors) * 35),
                            margin=dict(l=0, r=60, t=45, b=0),
                        )
                        st.plotly_chart(fig_sr_bar, use_container_width=True,
                                        key="sr_bar_chart")
                    else:
                        st.info(f"No prior-year sector data for {selected_year} to compute YoY%.")

                st.markdown("---")
                st.markdown("**Backtest: Top-Ramping Sector ETF vs S&P 500**")
                st.caption(
                    "Each row: the sector that ramped lobbying most in 'year', "
                    "its SPDR ETF, and the ETF's actual calendar-year return "
                    "for the *following* year (the holding period)."
                )

                # KPIs
                valid_sr = sr_df[sr_df["etf_return"].notna() & sr_df["spx_return"].notna()]
                if not valid_sr.empty:
                    avg_etf = valid_sr["etf_return"].mean()
                    avg_spx = valid_sr["spx_return"].mean()
                    avg_alpha = valid_sr["alpha"].mean() if valid_sr["alpha"].notna().any() else None
                    win_rate = (valid_sr["alpha"] > 0).mean() * 100

                    ksr1, ksr2, ksr3, ksr4 = st.columns(4)
                    with ksr1:
                        st.metric("Years Tracked", f"{len(valid_sr)}")
                    with ksr2:
                        st.metric("Avg ETF Return", f"{avg_etf:+.1f}%")
                    with ksr3:
                        st.metric("Avg S&P 500 Return", f"{avg_spx:+.1f}%")
                    with ksr4:
                        color_label = f"{avg_alpha:+.1f}%" if avg_alpha is not None else "N/A"
                        st.metric("Avg Alpha", color_label)

                # Chart
                if not valid_sr.empty:
                    fig_sr = go.Figure()
                    sr_years = valid_sr["year"].tolist()
                    fig_sr.add_trace(go.Bar(
                        x=sr_years,
                        y=valid_sr["etf_return"].tolist(),
                        name="Sector ETF (hold yr)",
                        marker_color="#60a5fa",
                        text=[f"{v:+.1f}%" for v in valid_sr["etf_return"]],
                        textposition="outside",
                    ))
                    fig_sr.add_trace(go.Bar(
                        x=sr_years,
                        y=valid_sr["spx_return"].tolist(),
                        name="S&P 500",
                        marker_color="#f97316",
                        text=[f"{v:+.1f}%" for v in valid_sr["spx_return"]],
                        textposition="outside",
                    ))
                    fig_sr.add_hline(y=0, line_color="#6b7280", line_width=1)
                    fig_sr.update_layout(
                        barmode="group",
                        xaxis=dict(title="Signal Year", dtick=1,
                                   gridcolor="#2d3748", tickvals=sr_years),
                        yaxis=dict(title="1-Year Return (%) — following year",
                                   gridcolor="#2d3748"),
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                    borderwidth=1, orientation="h",
                                    yanchor="bottom", y=1.02, xanchor="right", x=1),
                        height=380,
                        margin=dict(l=0, r=40, t=50, b=0),
                    )
                    st.plotly_chart(fig_sr, use_container_width=True,
                                    key="sr_backtest_chart")

                # Table
                display_sr = sr_df.copy()
                display_sr = display_sr.rename(columns={
                    "year": "Signal Year",
                    "top_sector": "Top Sector",
                    "etf_ticker": "ETF",
                    "sector_yoy_pct": "Sector YoY%",
                    "n_sectors": "Sectors Ranked",
                    "etf_return": "ETF Return (hold yr)",
                    "spx_return": "S&P 500 (hold yr)",
                    "alpha": "Alpha",
                })

                def _pct(v):
                    return f"{v:+.1f}%" if pd.notna(v) else "N/A"

                for col in ["Sector YoY%", "ETF Return (hold yr)", "S&P 500 (hold yr)", "Alpha"]:
                    if col in display_sr.columns:
                        display_sr[col] = display_sr[col].apply(_pct)

                st.dataframe(display_sr, use_container_width=True, hide_index=True)

                if "_fetch_error" in sr_df.columns:
                    st.warning(f"ETF data fetch error: {sr_df['_fetch_error'].iloc[0]}")

        # ── Signal 2: New Entrant Detection ───────────────────────────────
        with sig_s2:
            st.markdown("#### New Entrant / Spend Acceleration Signal")
            st.caption(
                "Flags companies whose lobbying spend crosses from near-zero to "
                "significant in a single year. The thesis: a company deploying "
                "material lobbying capital for the first time signals strategic "
                "regulatory engagement, which may precede regulatory wins or "
                "contract awards not yet priced in."
            )
            st.caption(
                "Backtest year is hold year (forward 12M return). "
                "Signal comes from the prior filing year."
            )

            ne_c1, ne_c2 = st.columns(2)
            with ne_c1:
                ne_min_curr = st.number_input(
                    "Min current-year spend ($M)",
                    min_value=0.5, max_value=10.0, value=1.0, step=0.5,
                    key="ne_min_curr",
                )
            with ne_c2:
                ne_max_prev = st.number_input(
                    "Max prior-year spend ($K)",
                    min_value=0.0, max_value=500.0, value=200.0, step=50.0,
                    key="ne_max_prev",
                )

            with st.spinner("Loading new entrant data..."):
                ne_detail_df, ne_bt_df = get_new_entrant_signal(
                    fetcher.db_path,
                    min_curr_spend_m=ne_min_curr,
                    max_prev_spend_k=ne_max_prev,
                    refresh_token=st.session_state["cache_buster"],
                )

            if ne_bt_df.empty:
                st.info(
                    "No new entrant backtest data available. "
                    "Stock performance data must be populated first "
                    "(use 'Populate Stock Performance' in Settings)."
                )
            else:
                valid_ne = ne_bt_df[
                    ne_bt_df["avg_return"].notna() & ne_bt_df["spx_return"].notna()
                ]
                if not valid_ne.empty:
                    avg_ne_ret = valid_ne["avg_return"].mean()
                    avg_ne_spx = valid_ne["spx_return"].mean()
                    avg_ne_alpha = valid_ne["alpha"].dropna().mean()
                    total_entrants = ne_detail_df["company_name"].nunique() if not ne_detail_df.empty else 0

                    kne1, kne2, kne3, kne4 = st.columns(4)
                    with kne1:
                        st.metric("Years in Backtest", f"{len(valid_ne)}")
                    with kne2:
                        st.metric("Unique New Entrants", f"{total_entrants}")
                    with kne3:
                        st.metric("Avg New Entrant Return", f"{avg_ne_ret:+.1f}%")
                    with kne4:
                        ne_alpha_str = f"{avg_ne_alpha:+.1f}%" if pd.notna(avg_ne_alpha) else "N/A"
                        st.metric("Avg Alpha vs S&P 500", ne_alpha_str)

                # Backtest chart
                if not valid_ne.empty:
                    fig_ne = go.Figure()
                    ne_years = valid_ne["year"].tolist()
                    fig_ne.add_trace(go.Bar(
                        x=ne_years,
                        y=valid_ne["avg_return"].tolist(),
                        name="New Entrants (equal-weight)",
                        marker_color="#a78bfa",
                        text=[f"{v:+.1f}%" for v in valid_ne["avg_return"]],
                        textposition="outside",
                        customdata=valid_ne["n_with_returns"].tolist(),
                        hovertemplate=(
                            "Hold Year %{x}<br>"
                            "New Entrant Return: %{y:+.1f}%<br>"
                            "N companies: %{customdata}<extra></extra>"
                        ),
                    ))
                    fig_ne.add_trace(go.Bar(
                        x=ne_years,
                        y=valid_ne["spx_return"].tolist(),
                        name="S&P 500",
                        marker_color="#f97316",
                        text=[f"{v:+.1f}%" for v in valid_ne["spx_return"]],
                        textposition="outside",
                    ))
                    fig_ne.add_hline(y=0, line_color="#6b7280", line_width=1)
                    fig_ne.update_layout(
                        barmode="group",
                        title="New Entrant 1-Year Forward Returns vs S&P 500",
                        xaxis=dict(title="Hold Year", dtick=1, gridcolor="#2d3748",
                                   tickvals=ne_years),
                        yaxis=dict(title="Equal-Weight 1Y Return (%)",
                                   gridcolor="#2d3748"),
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                    borderwidth=1, orientation="h",
                                    yanchor="bottom", y=1.02, xanchor="right", x=1),
                        height=380,
                        margin=dict(l=0, r=40, t=50, b=0),
                    )
                    st.plotly_chart(fig_ne, use_container_width=True,
                                    key="ne_backtest_chart")

                # Backtest table
                st.markdown("**Annual Backtest Summary**")
                bt_show = ne_bt_df.copy()
                bt_show = bt_show.rename(columns={
                    "signal_year": "Signal Year",
                    "year": "Hold Year",
                    "n_entrants": "New Entrants",
                    "n_with_returns": "W/ Returns",
                    "avg_return": "Avg Return",
                    "spx_return": "S&P 500",
                    "alpha": "Alpha",
                })
                for c in ["Avg Return", "S&P 500", "Alpha"]:
                    if c in bt_show.columns:
                        bt_show[c] = bt_show[c].apply(
                            lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                        )
                st.dataframe(bt_show, use_container_width=True,
                             hide_index=True, height=220)

            # Detail for selected year
            if not ne_detail_df.empty:
                st.markdown(f"---")
                st.markdown(f"**New Entrants in {selected_year}**")
                yr_ne = ne_detail_df[
                    ne_detail_df["signal_year"] == selected_year
                ].copy()
                if yr_ne.empty:
                    st.info(f"No new entrants detected for {selected_year} under current thresholds.")
                else:
                    yr_ne["curr_spend"] = yr_ne["curr_spend"].apply(format_currency)
                    yr_ne["prev_spend"] = yr_ne["prev_spend"].apply(format_currency)
                    yr_ne["return_1y"] = yr_ne["return_1y"].apply(
                        lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                    )
                    yr_ne = yr_ne.rename(columns={
                        "company_name": "Company",
                        "ticker": "Ticker",
                        "sector": "Sector",
                        "curr_spend": "Current Yr Spend",
                        "prev_spend": "Prior Yr Spend",
                        "return_1y": "1Y Fwd Return",
                    })
                    st.dataframe(
                        yr_ne.drop(columns=["year"], errors="ignore"),
                        use_container_width=True, hide_index=True,
                    )

        # ── Signal 3: Quality Gate on Production Picks ──────────────────────
        with sig_s3:
            st.markdown("#### Quality-Gated Production Picks")
            st.caption(
                "Applies a quality gate to the production watchlist "
                f"(signal year {int(selected_year)} -> hold year {int(selected_year) + 1}). "
                "This keeps the screen aligned with the live model instead of a separate score."
            )

            # Quality filter controls
            qf_col1, qf_col2, qf_col3 = st.columns(3)
            with qf_col1:
                qf_min_roe = st.number_input(
                    "Min ROE (%)",
                    min_value=-50.0,
                    max_value=100.0,
                    value=5.0,
                    step=1.0,
                    key="qf_min_roe",
                    help="Return on equity (proxy for ROIC). Set to -50 to disable.",
                )
            with qf_col2:
                qf_max_de = st.number_input(
                    "Max Debt/Equity Ratio (x)",
                    min_value=0.0,
                    max_value=20.0,
                    value=3.0,
                    step=0.5,
                    key="qf_max_de",
                    help=(
                        "Maximum debt-to-equity ratio. 3x means total debt "
                        "is no more than 3x equity. Set to 20 to disable."
                    ),
                )
            with qf_col3:
                qf_pos_eg = st.checkbox(
                    "Require positive earnings growth",
                    value=True,
                    key="qf_pos_eg",
                )

            prod_top_n_cfg = int(getattr(config, "PRODUCTION_TOP_N", 10))
            prod_min_usable_cfg = int(getattr(config, "PRODUCTION_MIN_USABLE_NAMES", 5))
            prod_primary_factor_cfg = str(
                getattr(config, "PRODUCTION_PRIMARY_FACTOR", "hist_spend_z")
            )
            prod_fallback_factor_cfg = str(
                getattr(config, "PRODUCTION_FALLBACK_FACTOR", "accel_score")
            )
            prod_hold_year_qf = int(selected_year) + 1

            prod_screen_df, prod_meta_qf = get_production_strategy_holdings(
                fetcher.db_path,
                hold_year=prod_hold_year_qf,
                top_n=prod_top_n_cfg,
                primary_factor=prod_primary_factor_cfg,
                fallback_factor=prod_fallback_factor_cfg,
                refresh_token=st.session_state["cache_buster"],
                min_spend_m=min_spend,
                min_usable_names=prod_min_usable_cfg,
            )

            if prod_screen_df.empty:
                st.info(
                    f"No production picks available for signal year {selected_year} "
                    f"(hold year {prod_hold_year_qf})."
                )
            else:
                prod_screen_df = prod_screen_df.copy().reset_index(drop=True)
                prod_screen_df["production_rank"] = prod_screen_df.index + 1

                quality_df = get_quality_metrics_from_db(
                    fetcher.db_path, refresh_token=st.session_state["cache_buster"]
                )

                tickers_in_screen = (
                    prod_screen_df["ticker"].dropna().astype(str).str.strip().unique().tolist()
                )
                already_fetched = (
                    quality_df["ticker"].astype(str).str.strip().tolist()
                    if not quality_df.empty
                    else []
                )
                stale_cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                stale_tickers = []
                if not quality_df.empty:
                    stale_mask = quality_df["fetched_at"] < stale_cutoff
                    stale_tickers = (
                        quality_df.loc[stale_mask, "ticker"]
                        .astype(str)
                        .str.strip()
                        .tolist()
                    )
                to_fetch_list = [
                    t for t in tickers_in_screen if t not in already_fetched or t in stale_tickers
                ]

                qf_btn_col, qf_info_col = st.columns([1, 3])
                with qf_btn_col:
                    run_qf_fetch = st.button(
                        f"Fetch Quality Data ({len(to_fetch_list)} tickers)",
                        use_container_width=True,
                        key="qf_fetch_btn",
                        disabled=(len(to_fetch_list) == 0),
                    )
                with qf_info_col:
                    if len(to_fetch_list) == 0:
                        st.caption(
                            "Quality data is current for this production watchlist. "
                            f"Model in use: {prod_meta_qf.get('model_used', 'N/A')}."
                        )
                    else:
                        st.caption(
                            f"{len(to_fetch_list)} ticker(s) need quality data. "
                            "Fetch pulls ROE, D/E, and earnings growth from Yahoo Finance "
                            "for this production list."
                        )

                if run_qf_fetch:
                    with st.spinner(
                        f"Fetching quality metrics for {len(to_fetch_list)} tickers..."
                    ):
                        fetch_quality_metrics_for_tickers(to_fetch_list, fetcher.db_path)
                    st.session_state["cache_buster"] += 1
                    quality_df = get_quality_metrics_from_db(
                        fetcher.db_path, refresh_token=st.session_state["cache_buster"]
                    )
                    st.success(f"Fetched quality data for {len(to_fetch_list)} tickers.")

                if quality_df.empty:
                    st.info(
                        "No quality data in database yet. "
                        "Click 'Fetch Quality Data' above to populate ROE, D/E, and "
                        "earnings growth for production picks."
                    )
                else:
                    merged_qf = prod_screen_df.merge(
                        quality_df[["ticker", "roe", "debt_to_equity", "earnings_growth", "fetched_at"]],
                        on="ticker",
                        how="left",
                    )

                    # Apply quality filters.
                    mask = pd.Series([True] * len(merged_qf), index=merged_qf.index)
                    roe_has_data = merged_qf["roe"].notna()
                    if qf_min_roe > -50:
                        mask &= (~roe_has_data) | (merged_qf["roe"] * 100 >= qf_min_roe)

                    de_has_data = merged_qf["debt_to_equity"].notna()
                    # Yahoo debtToEquity is percentage-point units (e.g. 175 = 1.75x).
                    mask &= (~de_has_data) | (merged_qf["debt_to_equity"] / 100 <= qf_max_de)

                    if qf_pos_eg:
                        eg_has_data = merged_qf["earnings_growth"].notna()
                        mask &= (~eg_has_data) | (merged_qf["earnings_growth"] > 0)

                    filtered_qf = merged_qf[mask].copy()
                    unfiltered_count = len(merged_qf)
                    filtered_count = len(filtered_qf)

                    kqf1, kqf2, kqf3, kqf4 = st.columns(4)
                    with kqf1:
                        st.metric("Before Filter", f"{unfiltered_count}")
                    with kqf2:
                        st.metric("After Filter", f"{filtered_count}")
                    with kqf3:
                        st.metric("Removed", f"{unfiltered_count - filtered_count}")
                    with kqf4:
                        pct_pass = (
                            filtered_count / unfiltered_count * 100
                            if unfiltered_count > 0
                            else 0
                        )
                        st.metric("Pass Rate", f"{pct_pass:.0f}%")

                    st.markdown("**Quality-Filtered Production Screen**")
                    st.caption(
                        "Rows with missing quality fields are kept (shown as —), "
                        "so the production list remains complete while highlighting "
                        "where manual review is needed."
                    )

                    disp_qf = filtered_qf.copy().sort_values("production_rank").head(50)
                    disp_qf["annual_spend"] = disp_qf["annual_spend"].apply(format_currency)
                    disp_qf["hist_spend_z"] = disp_qf["hist_spend_z"].apply(
                        lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                    )
                    disp_qf["accel_score"] = disp_qf["accel_score"].apply(
                        lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                    )
                    disp_qf["roe_pct"] = disp_qf["roe"].apply(
                        lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "—"
                    )
                    disp_qf["de_fmt"] = disp_qf["debt_to_equity"].apply(
                        lambda v: f"{v / 100:.2f}x" if pd.notna(v) else "—"
                    )
                    disp_qf["eg_fmt"] = disp_qf["earnings_growth"].apply(
                        lambda v: f"{v * 100:+.1f}%" if pd.notna(v) else "—"
                    )

                    show_cols = {
                        "production_rank": "Rank",
                        "company_name": "Company",
                        "ticker": "Ticker",
                        "sector": "Sector",
                        "annual_spend": "Spend",
                        "hist_spend_z": "Hist Z",
                        "accel_score": "Composite",
                        "roe_pct": "ROE",
                        "de_fmt": "D/E",
                        "eg_fmt": "Earnings Growth",
                        "fetched_at": "Data Date",
                    }
                    disp_qf = disp_qf.rename(
                        columns={k: v for k, v in show_cols.items() if k in disp_qf.columns}
                    )
                    display_columns = [v for v in show_cols.values() if v in disp_qf.columns]
                    st.dataframe(disp_qf[display_columns], use_container_width=True, hide_index=True)

                    st.download_button(
                        "Download Quality-Filtered Production CSV",
                        data=filtered_qf.to_csv(index=False).encode("utf-8"),
                        file_name=f"quality_filtered_production_{selected_year}.csv",
                        mime="text/csv",
                        key="dl_qf",
                    )

    # ══════════════════════════════════════════════════════════════════════════
    with tab5:
        st.markdown("### Company Research")
        st.caption(
            "Search by ticker symbol or company name to see a full lobbying and "
            "stock performance history for that entity."
        )

        # ── Search bar ────────────────────────────────────────────────────────
        cr_query = st.text_input(
            "Search ticker or company name",
            placeholder="e.g. MSFT, Microsoft, Pfizer, AMZN",
            key="cr_search_query",
        ).strip()

        if not cr_query:
            st.info(
                "Enter a ticker symbol or company name above to begin. "
                "Examples: MSFT, META, Pfizer, Lockheed, AMZN"
            )
        else:
            cr_results = search_companies(
                fetcher.db_path, cr_query,
                refresh_token=st.session_state["cache_buster"],
            )

            if cr_results.empty:
                st.warning(
                    f"No companies found matching '{cr_query}'. "
                    "Try the ticker symbol (e.g. MSFT) or a shorter name."
                )
            else:
                # ── Company selector ──────────────────────────────────────────
                if len(cr_results) == 1:
                    selected_cr = cr_results.iloc[0]
                else:
                    cr_options = {
                        f"{r['ticker']}  —  {r.get('display_name', r['ticker'])}  "
                        f"(${r['total_spend']/1e6:.1f}M total, "
                        f"{r['earliest_year']}–{r['latest_year']})": i
                        for i, r in cr_results.iterrows()
                    }
                    chosen_label = st.selectbox(
                        f"Found {len(cr_results)} match(es) — select one:",
                        options=list(cr_options.keys()),
                        key="cr_selector",
                    )
                    selected_cr = cr_results.loc[cr_options[chosen_label]]

                cr_ticker  = selected_cr["ticker"]
                cr_sector  = selected_cr.get("sector", "Unknown")
                cr_name    = selected_cr.get("display_name", cr_ticker)
                cr_yrs_act = int(selected_cr["years_active"])
                cr_earliest = int(selected_cr["earliest_year"])
                cr_latest   = int(selected_cr["latest_year"])

                # ── Load all data ─────────────────────────────────────────────
                with st.spinner(f"Loading data for {cr_ticker}..."):
                    cr_annual  = get_company_annual_spend(
                        fetcher.db_path, cr_ticker,
                        refresh_token=st.session_state["cache_buster"],
                    )
                    cr_stock   = get_company_stock_snapshots(
                        fetcher.db_path, cr_ticker,
                        refresh_token=st.session_state["cache_buster"],
                    )
                    cr_entities = get_company_entity_names(
                        fetcher.db_path, cr_ticker,
                        refresh_token=st.session_state["cache_buster"],
                    )
                    entity_key = "|".join(sorted(cr_entities))
                    cr_firms   = get_company_lobbyist_firms(
                        fetcher.db_path, entity_key,
                        refresh_token=st.session_state["cache_buster"],
                    )
                    cr_peers   = get_company_sector_peers(
                        fetcher.db_path, cr_sector, cr_ticker,
                        refresh_token=st.session_state["cache_buster"],
                    ) if cr_sector and cr_sector != "Unknown" else pd.DataFrame()

                # ── Company header ────────────────────────────────────────────
                st.markdown(f"---")
                st.markdown(f"### {cr_name}  (`{cr_ticker}`)")
                st.caption(
                    f"Sector: {cr_sector}  |  "
                    f"Data: {cr_earliest}–{cr_latest}  |  "
                    f"{cr_yrs_act} year(s) in database"
                )

                if cr_annual.empty:
                    st.warning("No aggregated lobbying data found for this ticker.")
                else:
                    latest_yr_row = cr_annual.iloc[-1]
                    prev_yr_row   = cr_annual.iloc[-2] if len(cr_annual) > 1 else None

                    latest_spend  = latest_yr_row["total"]
                    latest_yoy    = latest_yr_row["yoy_pct"]
                    latest_smcap  = latest_yr_row.get("spend_to_mcap_pct")
                    latest_yr_lbl = int(latest_yr_row["year"])

                    latest_ret = None
                    if not cr_stock.empty:
                        stock_row = cr_stock[cr_stock["year"] == latest_yr_lbl - 1]
                        if not stock_row.empty:
                            latest_ret = stock_row.iloc[0]["return_1y"]

                    # KPIs
                    k1, k2, k3, k4, k5 = st.columns(5)
                    with k1:
                        st.metric(
                            f"Total Spend ({latest_yr_lbl})",
                            format_currency(latest_spend),
                        )
                    with k2:
                        yoy_str = (
                            f"{latest_yoy:+.1f}%" if pd.notna(latest_yoy) else "N/A"
                        )
                        st.metric("YoY% Change", yoy_str)
                    with k3:
                        smcap_str = (
                            f"{latest_smcap:.3f}%"
                            if latest_smcap is not None and pd.notna(latest_smcap)
                            else "N/A"
                        )
                        st.metric("Spend / Market Cap", smcap_str)
                    with k4:
                        ret_str = (
                            f"{latest_ret:+.1f}%"
                            if latest_ret is not None and pd.notna(latest_ret)
                            else "N/A"
                        )
                        st.metric(f"1Y Stock Return ({latest_yr_lbl - 1})", ret_str)
                    with k5:
                        n_firms_latest = (
                            int(cr_firms[cr_firms["year"] == latest_yr_lbl]["n_firms"].iloc[0])
                            if not cr_firms.empty and latest_yr_lbl in cr_firms["year"].values
                            else None
                        )
                        st.metric(
                            "Lobbying Firms Hired",
                            str(n_firms_latest) if n_firms_latest is not None else "N/A",
                        )

                    st.markdown("---")

                    # ── Chart 1: Stacked quarterly spend + stock price overlay ─
                    st.markdown("#### Lobbying Spend History")
                    cr_years = cr_annual["year"].tolist()
                    q_colors = {"Q1": "#3b82f6", "Q2": "#60a5fa",
                                "Q3": "#93c5fd", "Q4": "#bfdbfe"}

                    fig_spend = go.Figure()
                    for q in ["Q1", "Q2", "Q3", "Q4"]:
                        if q in cr_annual.columns:
                            fig_spend.add_trace(go.Bar(
                                x=cr_years,
                                y=cr_annual[q].tolist(),
                                name=q,
                                marker_color=q_colors[q],
                                hovertemplate=(
                                    f"{q}: $%{{y:,.0f}}<extra></extra>"
                                ),
                            ))

                    # Stock price line on secondary y-axis
                    if not cr_stock.empty:
                        merged_stock = cr_annual[["year"]].merge(
                            cr_stock[["year", "close_price"]], on="year", how="left"
                        )
                        fig_spend.add_trace(go.Scatter(
                            x=merged_stock["year"].tolist(),
                            y=merged_stock["close_price"].tolist(),
                            name="Stock Price (Dec 31, $)",
                            mode="lines+markers",
                            line=dict(color="#f97316", width=2.5),
                            marker=dict(size=6),
                            yaxis="y2",
                            hovertemplate="Stock: $%{y:,.2f}<extra></extra>",
                        ))

                    fig_spend.update_layout(
                        barmode="stack",
                        xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748",
                                   tickvals=cr_years),
                        yaxis=dict(title="Lobbying Spend ($)", gridcolor="#2d3748",
                                   tickformat="$,.0f"),
                        yaxis2=dict(title="Stock Price ($)", overlaying="y",
                                    side="right", showgrid=False, tickformat="$,.0f"),
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                    borderwidth=1, orientation="h",
                                    yanchor="bottom", y=1.02, xanchor="right", x=1),
                        height=400,
                        margin=dict(l=0, r=60, t=50, b=0),
                    )
                    st.plotly_chart(fig_spend, use_container_width=True,
                                    key="cr_spend_chart")

                    # ── Chart 2: YoY% Lobbying vs 1Y Stock Return ─────────────
                    st.markdown("#### Lobbying Growth vs Stock Return")
                    st.caption(
                        "Bars show year-over-year lobbying spend change. "
                        "Orange line shows 1-year forward stock return for the same year. "
                        "A lead effect would appear as lobbying ramps before the stock return improves."
                    )

                    if not cr_stock.empty:
                        yoy_stock_df = cr_annual[["year", "yoy_pct"]].merge(
                            cr_stock[["year", "return_1y"]], on="year", how="left"
                        )
                    else:
                        yoy_stock_df = cr_annual[["year", "yoy_pct"]].copy()
                        yoy_stock_df["return_1y"] = None

                    fig_yoy = go.Figure()
                    bar_colors_yoy = [
                        "#22c55e" if (pd.notna(v) and v >= 0) else "#ef4444"
                        for v in yoy_stock_df["yoy_pct"]
                    ]
                    fig_yoy.add_trace(go.Bar(
                        x=yoy_stock_df["year"].tolist(),
                        y=yoy_stock_df["yoy_pct"].tolist(),
                        name="Lobbying YoY%",
                        marker_color=bar_colors_yoy,
                        text=[
                            f"{v:+.1f}%" if pd.notna(v) else ""
                            for v in yoy_stock_df["yoy_pct"]
                        ],
                        textposition="outside",
                        hovertemplate="Lobbying YoY: %{y:+.1f}%<extra></extra>",
                    ))

                    valid_ret = yoy_stock_df["return_1y"].notna()
                    if valid_ret.any():
                        fig_yoy.add_trace(go.Scatter(
                            x=yoy_stock_df["year"].tolist(),
                            y=yoy_stock_df["return_1y"].tolist(),
                            name="1Y Stock Return",
                            mode="lines+markers",
                            line=dict(color="#f97316", width=2.5, dash="dot"),
                            marker=dict(size=7, symbol="diamond"),
                            hovertemplate="1Y Return: %{y:+.1f}%<extra></extra>",
                        ))

                    fig_yoy.add_hline(y=0, line_color="#6b7280", line_width=1)
                    fig_yoy.update_layout(
                        xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748",
                                   tickvals=cr_years),
                        yaxis=dict(title="% Change", gridcolor="#2d3748"),
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                    borderwidth=1, orientation="h",
                                    yanchor="bottom", y=1.02, xanchor="right", x=1),
                        height=360,
                        margin=dict(l=0, r=40, t=50, b=0),
                    )
                    st.plotly_chart(fig_yoy, use_container_width=True,
                                    key="cr_yoy_chart")

                    # ── Chart 3: Spend / Market Cap ratio ─────────────────────
                    if cr_annual["spend_to_mcap_pct"].notna().any():
                        st.markdown("#### Lobbying Intensity (Spend / Market Cap)")
                        st.caption(
                            "Normalises lobbying spend by company size. "
                            "A rising ratio means the company is increasing its "
                            "lobbying commitment relative to its market value."
                        )
                        fig_smcap = go.Figure()
                        fig_smcap.add_trace(go.Scatter(
                            x=cr_years,
                            y=cr_annual["spend_to_mcap_pct"].tolist(),
                            mode="lines+markers",
                            line=dict(color="#a78bfa", width=2.5),
                            marker=dict(size=7),
                            fill="tozeroy",
                            fillcolor="rgba(167, 139, 250, 0.15)",
                            hovertemplate="Spend/MCap: %{y:.4f}%<extra></extra>",
                        ))
                        fig_smcap.update_layout(
                            xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748",
                                       tickvals=cr_years),
                            yaxis=dict(title="Spend / Market Cap (%)",
                                       gridcolor="#2d3748"),
                            plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                            font=dict(color="#ffffff", size=12),
                            height=280,
                            margin=dict(l=0, r=40, t=30, b=0),
                            showlegend=False,
                        )
                        st.plotly_chart(fig_smcap, use_container_width=True,
                                        key="cr_smcap_chart")

                    # ── Chart 4: Lobbying firms hired ─────────────────────────
                    if not cr_firms.empty:
                        st.markdown("#### External Lobbying Firms Hired")
                        st.caption(
                            "Number of unique lobbying firms (registrants) retained "
                            "per year. A sudden increase signals a major new "
                            "regulatory or legislative push."
                        )
                        fig_firms = go.Figure()
                        fig_firms.add_trace(go.Bar(
                            x=cr_firms["year"].tolist(),
                            y=cr_firms["n_firms"].tolist(),
                            name="Lobbying Firms",
                            marker_color="#34d399",
                            text=cr_firms["n_firms"].tolist(),
                            textposition="outside",
                            hovertemplate=(
                                "Year %{x}<br>"
                                "Firms: %{y}<br>"
                                "Paid: $%{customdata:,.0f}<extra></extra>"
                            ),
                            customdata=cr_firms["total_paid"].tolist(),
                        ))
                        fig_firms.update_layout(
                            xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748"),
                            yaxis=dict(title="# Lobbying Firms", gridcolor="#2d3748"),
                            plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                            font=dict(color="#ffffff", size=12),
                            height=280,
                            margin=dict(l=0, r=40, t=30, b=0),
                            showlegend=False,
                        )
                        st.plotly_chart(fig_firms, use_container_width=True,
                                        key="cr_firms_chart")

                    # ── Chart 5: Sector peer comparison ───────────────────────
                    if not cr_peers.empty:
                        st.markdown(f"#### Sector Peers — Lobbying Spend ({cr_sector})")
                        st.caption(
                            "Top 8 spenders in the same sector. "
                            "Shows whether this company is gaining or losing "
                            "lobbying share within its industry."
                        )
                        peer_tickers = cr_peers["ticker"].unique().tolist()
                        # Give the highlighted company a distinct colour
                        peer_palette = [
                            "#f97316" if t == cr_ticker else "#3b82f6"
                            for t in peer_tickers
                        ]

                        fig_peers = go.Figure()
                        for ticker_p, color_p in zip(peer_tickers, peer_palette):
                            td = cr_peers[cr_peers["ticker"] == ticker_p]
                            fig_peers.add_trace(go.Scatter(
                                x=td["year"].tolist(),
                                y=td["total"].tolist(),
                                name=ticker_p,
                                mode="lines+markers",
                                line=dict(
                                    color=color_p,
                                    width=3.0 if ticker_p == cr_ticker else 1.5,
                                ),
                                marker=dict(
                                    size=8 if ticker_p == cr_ticker else 5,
                                ),
                                hovertemplate=(
                                    f"{ticker_p}: $%{{y:,.0f}}<extra></extra>"
                                ),
                            ))

                        fig_peers.update_layout(
                            xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748"),
                            yaxis=dict(title="Annual Lobbying Spend ($)",
                                       gridcolor="#2d3748", tickformat="$,.0f"),
                            plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                            font=dict(color="#ffffff", size=12),
                            legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                        borderwidth=1, orientation="h",
                                        yanchor="bottom", y=1.02,
                                        xanchor="right", x=1),
                            height=360,
                            margin=dict(l=0, r=40, t=50, b=0),
                        )
                        st.plotly_chart(fig_peers, use_container_width=True,
                                        key="cr_peers_chart")

                    # ── Data table: annual summary ─────────────────────────────
                    st.markdown("#### Annual Summary Table")
                    summary_tbl = cr_annual.copy()
                    if not cr_stock.empty:
                        summary_tbl = summary_tbl.merge(
                            cr_stock[["year", "close_price", "return_1y"]],
                            on="year", how="left",
                        )
                    if not cr_firms.empty:
                        summary_tbl = summary_tbl.merge(
                            cr_firms[["year", "n_firms"]],
                            on="year", how="left",
                        )

                    def _fmt_spend(v):
                        return format_currency(v) if pd.notna(v) else "—"

                    def _fmt_pct(v):
                        return f"{v:+.1f}%" if pd.notna(v) else "—"

                    def _fmt_price(v):
                        return f"${v:,.2f}" if pd.notna(v) else "—"

                    disp_tbl = pd.DataFrame({
                        "Year": summary_tbl["year"].astype(int),
                        "Total Spend": summary_tbl["total"].apply(_fmt_spend),
                        "Q1": summary_tbl["Q1"].apply(_fmt_spend),
                        "Q2": summary_tbl["Q2"].apply(_fmt_spend),
                        "Q3": summary_tbl["Q3"].apply(_fmt_spend),
                        "Q4": summary_tbl["Q4"].apply(_fmt_spend),
                        "YoY%": summary_tbl["yoy_pct"].apply(_fmt_pct),
                        "Spend/MCap": summary_tbl["spend_to_mcap_pct"].apply(
                            lambda v: f"{v:.4f}%" if pd.notna(v) else "—"
                        ),
                    })
                    if "close_price" in summary_tbl.columns:
                        disp_tbl["Stock (Dec 31)"] = summary_tbl["close_price"].apply(
                            _fmt_price
                        )
                    if "return_1y" in summary_tbl.columns:
                        disp_tbl["1Y Return"] = summary_tbl["return_1y"].apply(
                            _fmt_pct
                        )
                    if "n_firms" in summary_tbl.columns:
                        disp_tbl["Lobbying Firms"] = summary_tbl["n_firms"].apply(
                            lambda v: str(int(v)) if pd.notna(v) else "—"
                        )

                    st.dataframe(
                        disp_tbl.sort_values("Year", ascending=False),
                        use_container_width=True, hide_index=True,
                    )

                    # Download
                    st.download_button(
                        "Download Company Research CSV",
                        data=summary_tbl.to_csv(index=False).encode("utf-8"),
                        file_name=f"{cr_ticker}_lobbying_research.csv",
                        mime="text/csv",
                        key="cr_download",
                    )

    # ══════════════════════════════════════════════════════════════════════════
    with tab6:
        st.markdown("### Settings & Data Management")

        # Load persisted settings into session_state once per session
        if "settings" not in st.session_state:
            st.session_state["settings"] = load_settings()
            # Apply saved overrides to config module so all app code uses them
            apply_settings_to_config(st.session_state["settings"])
        cfg = st.session_state["settings"]

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### Data Sources Configuration")

            with st.expander("Senate Lobbying Disclosure Database"):
                cfg["senate_api_endpoint"] = st.text_input(
                    "API Endpoint",
                    value=cfg["senate_api_endpoint"],
                    key="s_senate_endpoint",
                )
                cfg["senate_auto_update"] = st.checkbox(
                    "Auto-update on startup",
                    value=cfg["senate_auto_update"],
                    key="s_senate_auto_update",
                )
                cfg["senate_fetch_interval_hours"] = st.number_input(
                    "Fetch interval (hours)",
                    min_value=1,
                    max_value=168,
                    value=int(cfg["senate_fetch_interval_hours"]),
                    key="s_senate_interval",
                )
                cfg["lda_filing_lag_days"] = st.number_input(
                    "Filing lag assumption (days)",
                    min_value=0,
                    max_value=90,
                    value=int(cfg["lda_filing_lag_days"]),
                    key="s_lda_filing_lag_days",
                    help=(
                        "Days added after quarter-end before treating disclosures as "
                        "tradable signal data (default 20)."
                    ),
                )
                st.markdown("---")
                st.markdown("**SEC Ticker Universe (official listed issuers)**")
                cfg["sec_universe_auto_sync"] = st.checkbox(
                    "Auto-sync SEC universe during builds/backfills",
                    value=cfg["sec_universe_auto_sync"],
                    key="s_sec_auto_sync",
                )
                cfg["sec_universe_refresh_days"] = st.number_input(
                    "SEC refresh interval (days)",
                    min_value=1,
                    max_value=90,
                    value=int(cfg["sec_universe_refresh_days"]),
                    key="s_sec_refresh_days",
                )
                cfg["sec_user_agent"] = st.text_input(
                    "SEC User-Agent",
                    value=cfg["sec_user_agent"],
                    key="s_sec_user_agent",
                    help=(
                        "SEC requests should include a descriptive User-Agent with contact "
                        "information (per SEC fair access guidance)."
                    ),
                )
                st.caption(
                    "Ticker universe source: https://www.sec.gov/files/company_tickers_exchange.json"
                )

            with st.expander("OpenSecrets API"):
                cfg["opensecrets_api_key"] = st.text_input(
                    "API Key",
                    type="password",
                    value=cfg["opensecrets_api_key"],
                    placeholder="Enter API key",
                    key="s_opensecrets_key",
                )
                cfg["opensecrets_enabled"] = st.checkbox(
                    "Enable OpenSecrets data",
                    value=cfg["opensecrets_enabled"],
                    key="s_opensecrets_enabled",
                )
                st.caption(
                    "Register at [opensecrets.org/api/admin](https://www.opensecrets.org/api/admin/) "
                    "for a free API key. OpenSecrets provides PAC contributions and political "
                    "spending data to complement Senate LDA lobbying disclosures."
                )

                _os_test_col, _os_sync_col = st.columns(2)
                with _os_test_col:
                    if st.button("Test Connection", key="os_test_btn", use_container_width=True):
                        _key = cfg.get("opensecrets_api_key", "").strip()
                        if not _key:
                            st.warning("Enter an API key above first.")
                        else:
                            with st.spinner("Testing…"):
                                ok, msg = fetcher.test_opensecrets_connection(_key)
                            if ok:
                                st.success(msg)
                            else:
                                st.error(msg)

                with _os_sync_col:
                    _os_max = st.number_input(
                        "Max companies",
                        min_value=10, max_value=500,
                        value=100, step=10,
                        key="os_max_companies",
                        help="How many top lobbying companies to look up (rate-limit safety)",
                    )

                if st.button(
                    f"Sync OpenSecrets Data for {selected_year}",
                    key="os_sync_btn",
                    use_container_width=True,
                    disabled=not cfg.get("opensecrets_enabled", False),
                ):
                    _key = cfg.get("opensecrets_api_key", "").strip()
                    if not _key:
                        st.warning("Enter and save an API key first.")
                    else:
                        _progress = st.progress(0, text="Starting OpenSecrets sync…")
                        _status   = st.empty()

                        def _os_progress(cur, tot, name):
                            _progress.progress(
                                cur / tot,
                                text=f"[{cur}/{tot}] Looking up {name[:40]}…"
                            )
                            _status.caption(f"Processing: {name}")

                        _os_df = fetcher.fetch_opensecrets_data(
                            year=selected_year,
                            api_key=_key,
                            max_companies=int(_os_max),
                            progress_callback=_os_progress,
                        )
                        _progress.empty()
                        _status.empty()
                        if _os_df.empty:
                            st.warning("No OpenSecrets data returned. Check the API key.")
                        else:
                            _matched = _os_df["crp_id"].notna().sum()
                            st.success(
                                f"OpenSecrets sync complete: {_matched}/{len(_os_df)} "
                                f"companies matched. Data saved to database."
                            )
                            st.dataframe(
                                _os_df[["company_name","ticker","total_contribs","pacs","indivs","os_lobbying"]]
                                .rename(columns={
                                    "company_name": "Company",
                                    "ticker": "Ticker",
                                    "total_contribs": "Total Contribs ($)",
                                    "pacs": "PAC ($)",
                                    "indivs": "Individual ($)",
                                    "os_lobbying": "OS Lobbying ($)",
                                }),
                                use_container_width=True,
                                hide_index=True,
                                height=250,
                            )

            with st.expander("Market Data (Yahoo Finance)"):
                cfg["yfinance_realtime_mcap"] = st.checkbox(
                    "Enable real-time market cap",
                    value=cfg["yfinance_realtime_mcap"],
                    key="s_yf_mcap",
                )
                cfg["yfinance_price_history"] = st.checkbox(
                    "Enable price history",
                    value=cfg["yfinance_price_history"],
                    key="s_yf_history",
                )
                cfg["yfinance_history_years"] = st.number_input(
                    "History lookback (years)",
                    min_value=1,
                    max_value=10,
                    value=int(cfg["yfinance_history_years"]),
                    key="s_yf_years",
                )

            st.markdown("---")
            if st.button("Save Settings", use_container_width=True, type="primary"):
                st.session_state["settings"] = cfg
                if save_settings(cfg):
                    st.success("Settings saved to data/app_settings.json")

        with col2:
            st.markdown("#### Database Management")

            db_stats = get_database_stats(fetcher.db_path, st.session_state["cache_buster"])
            st.metric("Total Records", f"{db_stats['total_records']:,}")
            st.metric("Last Updated", db_stats["last_updated"] or "N/A")
            st.metric("Database Size", f"{db_stats['db_size_mb']:.1f} MB")

            st.markdown("---")
            st.markdown("#### SEC Ticker Universe")
            sec_stats = get_sec_universe_stats(
                fetcher.db_path, st.session_state["cache_buster"]
            )
            su1, su2, su3 = st.columns(3)
            su1.metric("Rows", f"{sec_stats['rows']:,}")
            su2.metric("Tickers", f"{sec_stats['distinct_tickers']:,}")
            su3.metric("Exchanges", f"{sec_stats['distinct_exchanges']}")
            st.caption(
                "Last SEC sync: "
                + (str(sec_stats["last_fetched_at"])[:16] if sec_stats["last_fetched_at"] else "Never")
            )
            if st.button("Refresh SEC Universe Now", use_container_width=True):
                with st.spinner("Fetching SEC ticker universe…"):
                    try:
                        from build_company_lobbying import sync_sec_ticker_universe

                        sec_sync = sync_sec_ticker_universe(
                            db_path=fetcher.db_path,
                            force=True,
                        )
                        st.success(
                            f"SEC universe synced: {sec_sync.get('inserted', 0):,} rows."
                        )
                        st.session_state["cache_buster"] += 1
                    except Exception as e:
                        st.error(f"SEC sync error: {e}")

            st.markdown("---")

            if st.button("Clear Cache", use_container_width=True):
                # Bump the token — all @st.cache_data functions keyed on
                # refresh_token will re-execute on next render.
                # (Avoid st.cache_data.clear() which triggers an async warning
                # in Streamlit ≥ 1.30 when called outside an async context.)
                st.session_state["cache_buster"] += 1
                st.success("Cache cleared — views will reload fresh data")

            st.markdown("---")
            st.markdown("#### Export Data")

            csv_bytes = get_company_lobbying_export_bytes(
                fetcher.db_path,
                refresh_token=st.session_state["cache_buster"],
            )
            st.download_button(
                label="Download company_lobbying CSV",
                data=csv_bytes,
                file_name=f"lobbying_data_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True,
            )

            st.markdown("---")

            st.markdown("#### Custom Ticker Mappings")
            st.caption(
                "One mapping per line: `Company Name,TICKER`  \n"
                "Persisted to `entity_aliases` so future backfills/rebuilds use them automatically."
            )
            mappings_csv = st.text_area(
                "Company-to-ticker mappings (CSV)",
                value=cfg.get("custom_ticker_mappings_csv", ""),
                placeholder="Company Name,Ticker\nExample Corp,EXAM\nAcme Lobbying LLC,ACME",
                height=180,
                key="s_custom_mappings",
            )
            cfg["custom_ticker_mappings_csv"] = mappings_csv

            if st.button("Import Mappings", use_container_width=True):
                if mappings_csv.strip():
                    added, errors = apply_custom_ticker_mappings(mappings_csv, fetcher.db_path)
                    save_settings(cfg)
                    if added:
                        st.success(
                            f"Imported {added} mapping(s) to alias table. "
                            "Run Ticker Backfill to apply across existing rows."
                        )
                        st.session_state["cache_buster"] += 1
                    if errors:
                        for err in errors:
                            st.warning(err)
                    if not added and not errors:
                        st.info("No valid mappings found in the input.")
                else:
                    st.warning("Paste at least one mapping before importing.")

            st.markdown("---")
            st.markdown("#### Data Hygiene")
            _bad_rows_df = get_nonpositive_spend_rows(
                fetcher.db_path, st.session_state["cache_buster"]
            )
            _bad_total = int(_bad_rows_df["bad_rows"].sum()) if not _bad_rows_df.empty else 0
            st.metric("Non-Positive Spend Rows", f"{_bad_total:,}")
            if _bad_total > 0:
                st.caption(
                    "These rows are legacy artifacts from earlier builds and can skew coverage metrics."
                )
                st.dataframe(
                    _bad_rows_df.rename(
                        columns={"year": "Year", "bad_rows": "Rows"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                    height=180,
                )
                if st.button("Purge Non-Positive Spend Rows", use_container_width=True):
                    with st.spinner("Cleaning company_lobbying rows with non-positive spend…"):
                        try:
                            from build_company_lobbying import clean_nonpositive_company_spend

                            clean_res = clean_nonpositive_company_spend(
                                db_path=fetcher.db_path, dry_run=False
                            )
                            st.success(
                                f"Removed {clean_res.get('rows_deleted', 0):,} non-positive rows."
                            )
                            st.session_state["cache_buster"] += 1
                        except Exception as e:
                            st.error(f"Data hygiene cleanup error: {e}")
            else:
                st.caption("No non-positive spend rows detected.")

            st.markdown("---")
            st.markdown("#### Alias Review Queue")
            st.caption("Top unmatched aliases by spend. Add mappings above, then run Ticker Backfill.")
            _alias_queue = get_alias_review_queue(
                fetcher.db_path, st.session_state["cache_buster"], limit=150
            )
            if _alias_queue.empty:
                st.info("No unmatched alias queue rows found.")
            else:
                _alias_show = _alias_queue.copy()
                _alias_show["total_spend"] = _alias_show["total_spend"].apply(format_currency)
                _alias_show["likely_non_public"] = _alias_show["likely_non_public"].apply(
                    lambda x: "Yes" if bool(x) else "No"
                )
                _alias_show["reviewed_ticker"] = (
                    _alias_show["reviewed_ticker"].fillna("").astype(str).str.strip()
                )
                _alias_show["suggested_ticker"] = (
                    _alias_show["suggested_ticker"].fillna("").astype(str).str.strip()
                )
                _alias_show["suggested_method"] = (
                    _alias_show["suggested_method"].fillna("").astype(str).str.strip()
                )

                _alias_show = _alias_show.rename(
                    columns={
                        "alias_name": "Alias",
                        "first_year": "First Year",
                        "latest_year": "Latest Year",
                        "years_present": "Years",
                        "total_spend": "Total Spend",
                        "row_count": "Rows",
                        "review_status": "Review Status",
                        "reviewed_ticker": "Reviewed Ticker",
                        "suggested_ticker": "Suggested Ticker",
                        "suggested_method": "Suggested Method",
                        "likely_non_public": "Likely Non-Public",
                    }
                )
                _alias_cols = [
                    "Alias",
                    "Total Spend",
                    "Rows",
                    "First Year",
                    "Latest Year",
                    "Years",
                    "Likely Non-Public",
                    "Reviewed Ticker",
                    "Suggested Ticker",
                    "Suggested Method",
                    "Review Status",
                ]
                st.dataframe(
                    _alias_show[_alias_cols],
                    use_container_width=True,
                    hide_index=True,
                    height=320,
                )
                _auto_suggested = int(
                    _alias_queue["suggested_ticker"].notna().sum()
                )
                _flagged_non_public = int(_alias_queue["likely_non_public"].sum())
                st.caption(
                    f"Auto suggestions: {_auto_suggested:,} aliases · "
                    f"Likely non-public: {_flagged_non_public:,} aliases"
                )
                st.download_button(
                    "Download Alias Queue CSV",
                    data=_alias_queue.to_csv(index=False).encode("utf-8"),
                    file_name="alias_review_queue.csv",
                    mime="text/csv",
                    key="dl_alias_queue",
                )

            st.markdown("---")
            st.markdown("#### Fuzzy Match Audit")
            st.caption(
                "Companies whose ticker was assigned via fuzzy matching. "
                "Review these before running backfills — false positives skew production rankings."
            )
            _audit_df = _get_fuzzy_audit(fetcher.db_path, st.session_state["cache_buster"])
            if _audit_df.empty:
                st.info("No fuzzy-matched tickers found (or match_method column not populated yet — run a backfill first).")
            else:
                st.dataframe(_audit_df, use_container_width=True, hide_index=True, height=300)
                st.download_button(
                    "Download Fuzzy Audit CSV",
                    data=_audit_df.to_csv(index=False).encode("utf-8"),
                    file_name="fuzzy_match_audit.csv",
                    mime="text/csv",
                    key="dl_fuzzy_audit",
                )

            st.markdown("---")
            st.markdown("#### Ingestion Metadata")
            st.caption(
                "Backfill audit rows for legacy years loaded before ingestion tracking was introduced."
            )
            if st.button("Backfill Legacy Ingestion Metadata", use_container_width=True):
                with st.spinner("Backfilling ingestion audit rows…"):
                    try:
                        from build_company_lobbying import backfill_ingestion_metadata

                        bf = backfill_ingestion_metadata(
                            db_path=fetcher.db_path,
                            dry_run=False,
                        )
                        st.success(
                            f"Backfill complete: added {bf['rows_added']:,} ingestion row(s)."
                        )
                        st.session_state["cache_buster"] += 1
                    except Exception as e:
                        st.error(f"Ingestion metadata backfill error: {e}")

            st.markdown("---")
            _ingestion_df = get_latest_ingestion_status(fetcher.db_path, st.session_state["cache_buster"])
            if not _ingestion_df.empty:
                _incomplete = _ingestion_df[_ingestion_df["is_complete"] == 0]["year"].tolist()
                _unknown = _ingestion_df[_ingestion_df["is_complete"].isna()]["year"].tolist()
                if _incomplete:
                    st.markdown("#### ⚠️ Incomplete Year Data Detected")
                    st.warning(
                        f"Year(s) **{', '.join(str(y) for y in sorted(_incomplete))}** "
                        "appear to have an incomplete fetch (the scraper did not reach the "
                        "natural end of the API). Use **'Fetch Data for Selected Year'** in "
                        "the sidebar to force a full re-fetch for those years."
                    )
                if _unknown:
                    st.markdown("#### ℹ️ Legacy Year Snapshots")
                    st.info(
                        f"Year(s) **{', '.join(str(y) for y in sorted(_unknown))}** "
                        "have no ingestion audit metadata yet. Data exists, but completeness "
                        "is unverified until you run a force refresh for those years."
                    )
                if not _incomplete and not _unknown:
                    st.success("All fetched years have complete data coverage.")
    
    # Footer
    st.markdown("---")
    st.markdown(
        "<p style='text-align: center; color: #a0a0a0; font-size: 12px;'>"
        "Data sources: Senate Lobbying Disclosure, OpenSecrets.org, Yahoo Finance | "
        "Strategy based on public lobbying expenditure filings | "
        "For informational purposes only"
        "</p>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
