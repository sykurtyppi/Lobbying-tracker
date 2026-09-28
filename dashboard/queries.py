"""
Cached SQLite read helpers: years, sectors, ingestion status, alias queue, DB stats, issue codes.
"""

import os
from datetime import datetime

import pandas as pd
import streamlit as st

from data_fetcher import LobbyingDataFetcher

from dashboard.formatting import format_currency


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Initialize data fetcher
@st.cache_resource
def get_data_fetcher():
    import os
    db_path = os.path.join(_REPO_ROOT, "data", "lobbying_data.db")
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


@st.cache_data
def get_issue_breakdown_for_ticker(db_path, ticker: str, refresh_token=0) -> pd.DataFrame:
    """
    Return the lobbying issue-code breakdown for a given ticker, aggregated
    by year and general_issue_code.

    Requires the filing_issues table (populated by build_company_lobbying_for_year).
    Returns empty DataFrame if the table doesn't exist yet (pre-migration data).

    Columns: year, general_issue_code, n_activities
    """
    import sqlite3

    if not ticker:
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(db_path)
        try:
            tbl = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='filing_issues'",
                conn,
            )
            if tbl.empty:
                return pd.DataFrame()
            df = pd.read_sql_query(
                """
                SELECT
                    fi.year,
                    fi.general_issue_code,
                    COUNT(*) AS n_activities
                FROM filing_issues fi
                WHERE UPPER(TRIM(fi.client_name)) IN (
                    SELECT UPPER(TRIM(company_name))
                    FROM company_lobbying
                    WHERE UPPER(TRIM(ticker)) = UPPER(TRIM(?))
                )
                GROUP BY fi.year, fi.general_issue_code
                ORDER BY fi.year DESC, n_activities DESC
                """,
                conn,
                params=(ticker,),
            )
            return df
        finally:
            conn.close()
    except Exception:
        return pd.DataFrame()


@st.cache_data
def get_top_issue_codes_by_year(db_path, year: int, top_n: int = 10, refresh_token=0) -> pd.DataFrame:
    """
    Return the top lobbying issue codes for ticker-matched (investable) companies only.

    Filters to clients whose company_name appears in company_lobbying with a non-null
    ticker, removing NGOs, trade associations, and government entities that are not
    investable but would otherwise dominate the issue-code counts.

    Requires the filing_issues table.
    Columns: general_issue_code, n_activities, n_companies
    """
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        try:
            tbl = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='filing_issues'",
                conn,
            )
            if tbl.empty:
                return pd.DataFrame()
            df = pd.read_sql_query(
                """
                SELECT
                    fi.general_issue_code,
                    COUNT(*)                      AS n_activities,
                    COUNT(DISTINCT fi.client_name) AS n_companies
                FROM filing_issues fi
                WHERE fi.year = ?
                  AND fi.client_name IN (
                      SELECT DISTINCT company_name
                      FROM company_lobbying
                      WHERE ticker IS NOT NULL
                        AND TRIM(ticker) != ''
                        AND year = ?
                  )
                GROUP BY fi.general_issue_code
                ORDER BY n_activities DESC
                LIMIT ?
                """,
                conn,
                params=(year, year, top_n),
            )
            return df
        finally:
            conn.close()
    except Exception:
        return pd.DataFrame()
