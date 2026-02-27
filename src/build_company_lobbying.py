# src/build_company_lobbying.py

import sqlite3
import time
from datetime import datetime, timezone
import pandas as pd
from typing import Optional
import requests
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from src.senate_scraper import SenateLobbyingScraper
from src.data_fetcher import LobbyingDataFetcher, CompanyMapper
from src.market_data_yahoo import fetch_market_cap_with_backoff
import config

PERIOD_TO_QUARTER = {
    "q1": "Q1",
    "first_quarter": "Q1",
    "q2": "Q2",
    "second_quarter": "Q2",
    "q3": "Q3",
    "third_quarter": "Q3",
    "q4": "Q4",
    "fourth_quarter": "Q4",
}


def quarter_from_period(period: str) -> Optional[str]:
    """Convert Senate filing_period values into canonical Q1..Q4."""
    if not period:
        return None

    normalized = str(period).strip().lower()
    return PERIOD_TO_QUARTER.get(normalized)


def is_allowed_filing_type(filing_type: Optional[str], allowed_types) -> bool:
    """Check filing type using case-insensitive substring matching."""
    if not allowed_types:
        return True

    filing_type_normalized = str(filing_type or "").strip().upper()
    if not filing_type_normalized:
        return False

    for allowed in allowed_types:
        if str(allowed).strip().upper() in filing_type_normalized:
            return True
    return False


def get_or_fetch_ticker_info(conn: sqlite3.Connection, ticker: str) -> dict:
    """
    Get ticker metadata (market_cap + sector) from the local cache
    (ticker_market_caps table), or fetch from Yahoo Finance and cache it.

    Returns:
        dict with keys:
            'market_cap'  – float | None
            'sector'      – str   | None  (e.g. "Technology", "Healthcare")
    """
    import yfinance as yf

    cur = conn.cursor()

    # Ensure cache table exists with sector column (idempotent)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ticker_market_caps (
            ticker TEXT PRIMARY KEY,
            market_cap REAL,
            sector TEXT,
            last_updated TEXT
        )
        """
    )
    # Migrate existing DBs that pre-date the sector column
    cur.execute("PRAGMA table_info(ticker_market_caps)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "sector" not in existing_cols:
        cur.execute("ALTER TABLE ticker_market_caps ADD COLUMN sector TEXT")
        conn.commit()

    # 1) Return any cache hit — even if market_cap is None (e.g. delisted tickers).
    #    Do NOT re-fetch on a cached None; repeated Yahoo calls for delisted symbols
    #    always fail and generate noisy error output every quarter they appear.
    cur.execute(
        "SELECT market_cap, sector FROM ticker_market_caps WHERE ticker = ?",
        (ticker,),
    )
    row = cur.fetchone()
    if row is not None:
        return {"market_cap": row[0], "sector": row[1]}

    yfinance_realtime_enabled = bool(
        getattr(
            config,
            "YFINANCE_REALTIME_MCAP_ENABLED",
            getattr(config, "YFINANCE_ENABLED", True),
        )
    )
    if not yfinance_realtime_enabled:
        return {"market_cap": None, "sector": None}

    # 2) Fetch from Yahoo Finance — get both market_cap and sector in one call
    market_cap: Optional[float] = None
    sector: Optional[str] = None
    try:
        info = yf.Ticker(ticker).info
        raw_cap = info.get("marketCap")
        market_cap = float(raw_cap) if raw_cap else None
        sector = info.get("sector") or None
    except Exception:
        # Fall back to the legacy backoff helper for market cap only
        market_cap = fetch_market_cap_with_backoff(ticker)

    # 3) Cache result (even None — avoids repeated failed lookups)
    cur.execute(
        """
        INSERT INTO ticker_market_caps (ticker, market_cap, sector, last_updated)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            market_cap   = excluded.market_cap,
            sector       = excluded.sector,
            last_updated = excluded.last_updated
        """,
        (ticker, market_cap, sector, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    return {"market_cap": market_cap, "sector": sector}


def get_or_fetch_market_cap(conn: sqlite3.Connection, ticker: str) -> Optional[float]:
    """
    Get market cap from local cache or Yahoo Finance.
    Thin wrapper around get_or_fetch_ticker_info() for backward compatibility.
    """
    return get_or_fetch_ticker_info(conn, ticker)["market_cap"]


def _ensure_sec_ticker_universe_table(conn: sqlite3.Connection) -> None:
    """Create SEC ticker universe table + indexes if missing."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sec_ticker_universe (
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            cik TEXT,
            exchange TEXT,
            is_etf INTEGER DEFAULT 0,
            source_url TEXT,
            fetched_at TEXT,
            PRIMARY KEY (ticker, company_name)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sec_ticker_universe_normalized ON sec_ticker_universe(normalized_name)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sec_ticker_universe_exchange ON sec_ticker_universe(exchange)"
    )
    conn.commit()


def sync_sec_ticker_universe(db_path: str = None, force: bool = False) -> dict:
    """
    Sync SEC's official exchange-listed ticker universe into SQLite.

    Source:
      https://www.sec.gov/files/company_tickers_exchange.json

    Returns
    -------
    dict with keys:
      status, inserted, source_count, skipped, last_fetched_at
    """
    if db_path is None:
        db_path = config.DATABASE_PATH

    refresh_days = max(int(getattr(config, "SEC_UNIVERSE_REFRESH_DAYS", 14)), 1)
    sec_url = getattr(
        config,
        "SEC_TICKER_UNIVERSE_URL",
        "https://www.sec.gov/files/company_tickers_exchange.json",
    )
    user_agent = getattr(
        config,
        "SEC_USER_AGENT",
        "LobbyingTracker/1.0 (research@localhost)",
    )
    timeout_seconds = max(int(getattr(config, "SEC_REQUEST_TIMEOUT_SECONDS", 45)), 10)

    # Check staleness before making a network call.
    conn = sqlite3.connect(db_path)
    try:
        _ensure_sec_ticker_universe_table(conn)
        latest_row = pd.read_sql_query(
            """
            SELECT MAX(fetched_at) AS last_fetched_at,
                   COUNT(*) AS row_count
            FROM sec_ticker_universe
            """,
            conn,
        ).iloc[0]
        last_fetched_at = latest_row.get("last_fetched_at")
        row_count = int(latest_row.get("row_count") or 0)
    finally:
        conn.close()

    if not force and last_fetched_at and row_count > 0:
        try:
            last_dt = pd.to_datetime(last_fetched_at, utc=True)
            age_days = (datetime.now(timezone.utc) - last_dt).days
            if age_days < refresh_days:
                return {
                    "status": "skipped_fresh",
                    "inserted": row_count,
                    "source_count": None,
                    "skipped": True,
                    "last_fetched_at": str(last_fetched_at),
                }
        except Exception:
            pass

    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json",
    }
    payload = None
    last_error = None
    for attempt in range(1, 4):
        try:
            response = requests.get(sec_url, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    if payload is None:
        raise RuntimeError(f"SEC universe fetch failed after retries: {last_error}")

    fields = payload.get("fields", []) if isinstance(payload, dict) else []
    data_rows = payload.get("data", []) if isinstance(payload, dict) else []
    if not fields or not isinstance(data_rows, list):
        raise ValueError(
            "Unexpected SEC ticker payload format; expected {'fields': [...], 'data': [...]}."
        )

    etf_like_tokens = (
        " ETF",
        " EXCHANGE TRADED FUND",
        " FUND",
        " TRUST",
        " PORTFOLIO",
        " INDEX",
        " NOTE",
        " BOND",
        " WARRANT",
        " RIGHTS",
    )
    fetched_at = datetime.now(timezone.utc).isoformat()

    parsed_rows = []
    seen = set()
    for raw_row in data_rows:
        if not isinstance(raw_row, (list, tuple)):
            continue
        row = dict(zip(fields, raw_row))

        ticker = str(row.get("ticker") or "").strip().upper()
        company_name = str(row.get("name") or row.get("title") or "").strip()
        exchange = str(row.get("exchange") or "").strip() or None
        cik_raw = row.get("cik") or row.get("cik_str")

        if not ticker or not company_name:
            continue

        normalized_name = CompanyMapper._normalize_company_name(company_name)
        if not normalized_name:
            continue

        cik = None
        if cik_raw is not None and str(cik_raw).strip():
            cik = str(cik_raw).strip()

        upper_name = f" {company_name.upper()} "
        is_etf = int(any(token in upper_name for token in etf_like_tokens))

        unique_key = (ticker, company_name)
        if unique_key in seen:
            continue
        seen.add(unique_key)

        parsed_rows.append(
            (
                ticker,
                company_name,
                normalized_name,
                cik,
                exchange,
                is_etf,
                sec_url,
                fetched_at,
            )
        )

    # Safety guard: SEC universe should contain thousands of rows.
    # If parsing yields a tiny set, abort rather than overwrite a healthy cache.
    if len(parsed_rows) < 1000:
        raise ValueError(
            f"SEC universe parse produced too few rows ({len(parsed_rows)}); aborting sync."
        )

    conn = sqlite3.connect(db_path)
    try:
        _ensure_sec_ticker_universe_table(conn)
        cursor = conn.cursor()
        cursor.execute("BEGIN")
        cursor.execute("DELETE FROM sec_ticker_universe")
        cursor.executemany(
            """
            INSERT INTO sec_ticker_universe
            (ticker, company_name, normalized_name, cik, exchange, is_etf, source_url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            parsed_rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "status": "synced",
        "inserted": len(parsed_rows),
        "source_count": len(data_rows),
        "skipped": False,
        "last_fetched_at": fetched_at,
    }


def _start_ingestion_run(db_path: str, year: int) -> int:
    """Create a new ingestion run row and return its id."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        now_iso = datetime.now(timezone.utc).isoformat()
        cur.execute(
            """
            INSERT INTO ingestion_runs
            (year, started_at, status, is_complete)
            VALUES (?, ?, 'running', 0)
            """,
            (year, now_iso),
        )
        run_id = cur.lastrowid
        conn.commit()
        return int(run_id)
    finally:
        conn.close()


def _finish_ingestion_run(
    db_path: str,
    run_id: int,
    *,
    status: str,
    api_reported_count: Optional[int] = None,
    fetched_count: Optional[int] = None,
    filtered_count: Optional[int] = None,
    inserted_raw_count: Optional[int] = None,
    aggregate_rows: Optional[int] = None,
    is_complete: bool = False,
    notes: Optional[str] = None,
) -> None:
    """Finalize ingestion run metadata."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ingestion_runs
            SET finished_at       = ?,
                status            = ?,
                api_reported_count= ?,
                fetched_count     = ?,
                filtered_count    = ?,
                inserted_raw_count= ?,
                aggregate_rows    = ?,
                is_complete       = ?,
                notes             = ?
            WHERE id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                status,
                api_reported_count,
                fetched_count,
                filtered_count,
                inserted_raw_count,
                aggregate_rows,
                1 if is_complete else 0,
                notes,
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def backfill_ingestion_metadata(db_path: str = None, dry_run: bool = False) -> dict:
    """
    Create ingestion_runs entries for legacy years that already have
    company_lobbying data but no ingestion audit row.

    This does NOT mark years complete; it sets status='legacy_snapshot'
    and is_complete=NULL so the UI can distinguish unknown provenance.
    """
    if db_path is None:
        db_path = config.DATABASE_PATH

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER,
                started_at TEXT,
                finished_at TEXT,
                status TEXT,
                api_reported_count INTEGER,
                fetched_count INTEGER,
                filtered_count INTEGER,
                inserted_raw_count INTEGER,
                aggregate_rows INTEGER,
                is_complete INTEGER,
                notes TEXT
            )
            """
        )

        yearly = pd.read_sql_query(
            """
            SELECT
                year,
                COUNT(*) AS row_count,
                MAX(last_updated) AS last_updated
            FROM company_lobbying
            GROUP BY year
            ORDER BY year
            """,
            conn,
        )
        existing_runs = pd.read_sql_query(
            "SELECT DISTINCT year FROM ingestion_runs",
            conn,
        )
        existing_years = set(
            pd.to_numeric(existing_runs["year"], errors="coerce").dropna().astype(int).tolist()
        )

        missing_rows = []
        for _, row in yearly.iterrows():
            year_val = pd.to_numeric(row.get("year"), errors="coerce")
            if pd.isna(year_val):
                continue
            year = int(year_val)
            if year in existing_years:
                continue
            last_updated = row.get("last_updated")
            ts = (
                str(last_updated)
                if last_updated and str(last_updated).strip()
                else datetime.now(timezone.utc).isoformat()
            )
            missing_rows.append(
                (
                    year,
                    ts,
                    ts,
                    "legacy_snapshot",
                    None,
                    None,
                    None,
                    None,
                    int(row.get("row_count") or 0),
                    None,
                    "Backfilled audit row from existing company_lobbying snapshot",
                )
            )

        if not dry_run and missing_rows:
            cursor.executemany(
                """
                INSERT INTO ingestion_runs
                (year, started_at, finished_at, status,
                 api_reported_count, fetched_count, filtered_count,
                 inserted_raw_count, aggregate_rows, is_complete, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                missing_rows,
            )
            conn.commit()

        return {
            "years_checked": int(len(yearly)),
            "rows_added": int(len(missing_rows)),
            "dry_run": bool(dry_run),
        }
    finally:
        conn.close()


def clean_nonpositive_company_spend(db_path: str = None, dry_run: bool = True) -> dict:
    """
    Remove invalid non-positive spend rows from company_lobbying.

    These rows are legacy artifacts from old builds before strict amount
    filtering. Keeping them inflates entity counts and weakens coverage stats.

    Args:
        db_path: Path to SQLite DB. Defaults to config.DATABASE_PATH.
        dry_run: If True, only report what would be deleted.

    Returns:
        dict with total rows flagged/deleted and per-year breakdown.
    """
    if db_path is None:
        db_path = config.DATABASE_PATH

    conn = sqlite3.connect(db_path)
    try:
        yearly = pd.read_sql_query(
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
        rows_flagged = int(
            pd.to_numeric(yearly["bad_rows"], errors="coerce").fillna(0).sum()
        ) if not yearly.empty else 0

        rows_deleted = 0
        if not dry_run and rows_flagged > 0:
            before_changes = conn.total_changes
            conn.execute(
                """
                DELETE FROM company_lobbying
                WHERE total_lobbying_spend IS NULL
                   OR total_lobbying_spend <= 0
                """
            )
            conn.commit()
            rows_deleted = int(conn.total_changes - before_changes)

        years_impacted = []
        if not yearly.empty:
            years_impacted = (
                pd.to_numeric(yearly["year"], errors="coerce")
                .dropna()
                .astype(int)
                .tolist()
            )

        return {
            "rows_flagged": rows_flagged,
            "rows_deleted": rows_deleted,
            "years_impacted": years_impacted,
            "year_breakdown": yearly.to_dict("records"),
            "dry_run": bool(dry_run),
        }
    finally:
        conn.close()


def build_company_lobbying_for_year(
    year: int,
    db_path: str = None,
    force: bool = False,
    force_partial: bool = False,
    max_pages: Optional[int] = None,
):
    """
    Fetch all filings for `year`, aggregate by client, join with market data,
    and populate `company_lobbying` table.

    Args:
        year:          Year to fetch.
        db_path:       Path to SQLite database.
        force:         If True, refetch even if a *complete* snapshot exists.
        force_partial: If True, write to DB even when the fetch did not reach the
                       natural API end (bypasses STRICT_FETCH_COMPLETENESS gate).
                       Useful when you know a partial fetch still has enough data.
        max_pages:     Senate API pagination safety cap.
    """
    if db_path is None:
        db_path = config.DATABASE_PATH
    if max_pages is None:
        max_pages = getattr(config, "SENATE_MAX_PAGES", 1200)
    allowed_filing_types = getattr(config, "SENATE_ALLOWED_FILING_TYPES", ["LD-2"])
    keep_latest_only = getattr(config, "SENATE_KEEP_LATEST_AMENDMENT_ONLY", True)
    public_company_only = getattr(config, "PUBLIC_COMPANY_ONLY", False)

    # ------------------------------------------------------------------
    # 0) Check if we already have data for this year
    # ------------------------------------------------------------------
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM company_lobbying WHERE year = ?",
            (year,),
        )
        existing_rows = cursor.fetchone()[0]

        skip_this_year = False
        if existing_rows > 0 and not force:
            # Check whether the most recent ingestion run for this year was complete.
            # If it was incomplete (e.g. a partial mid-year fetch), automatically
            # re-fetch rather than silently sitting on stale partial data.
            cursor.execute(
                """
                SELECT is_complete FROM ingestion_runs
                WHERE year = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (year,),
            )
            run_row = cursor.fetchone()
            # If no ingestion_run record exists, treat pre-existing rows as complete
            # (they were inserted before the audit trail was added).
            last_run_complete = bool(run_row[0]) if run_row is not None else True

            if last_run_complete:
                cursor.execute(
                    "SELECT MAX(last_updated) FROM company_lobbying WHERE year = ?",
                    (year,),
                )
                last_updated = cursor.fetchone()[0]
                print(f"\n{'=' * 70}")
                print(f"⏭  Skipping year {year} (complete snapshot already present)")
                print(f"{'=' * 70}")
                print(f"   Rows in company_lobbying : {existing_rows}")
                print(f"   Last updated             : {last_updated}")
                print(
                    f"\n   To force a full refresh  : build_company_lobbying_for_year({year}, force=True)"
                )
                print(f"{'=' * 70}\n")
                skip_this_year = True
            else:
                print(f"\n{'=' * 70}")
                print(
                    f"🔄  Re-fetching year {year} — previous ingestion was incomplete."
                )
                print(
                    f"   Existing {existing_rows} rows will be replaced on success."
                )
                print(f"{'=' * 70}\n")
    finally:
        conn.close()

    if skip_this_year:
        return

    scraper = SenateLobbyingScraper()
    # Keep fetcher around in case you want it later (but not used for mcap now)
    # Ensure tables exist
    LobbyingDataFetcher(db_path=db_path)
    if getattr(config, "SEC_UNIVERSE_AUTO_SYNC", True):
        try:
            sec_sync = sync_sec_ticker_universe(db_path=db_path, force=False)
            if sec_sync.get("status") == "synced":
                print(
                    f"✅ Synced SEC ticker universe: {sec_sync.get('inserted', 0):,} rows"
                )
        except Exception as exc:
            print(f"⚠️  SEC universe sync skipped due to error: {exc}")
    run_id = _start_ingestion_run(db_path, year)
    mapper = CompanyMapper(db_path=db_path)
    strict_fetch_completeness = getattr(config, "STRICT_FETCH_COMPLETENESS", True)

    fetch_meta = {}
    filtered_count = 0
    aggregate_rows = 0
    inserted_raw = 0

    print(f"\n{'=' * 70}")
    print(f"Building company_lobbying for {year}")
    print(f"{'=' * 70}\n")

    # ------------------------------------------------------------------
    # 1) Fetch all filings from Senate API for the year
    # ------------------------------------------------------------------
    print("📥 Fetching lobbying filings from Senate database...")
    filings = scraper.get_filings(filing_year=year, max_pages=max_pages)
    fetch_meta = getattr(scraper, "last_fetch_meta", {}) or {}
    api_reported = fetch_meta.get("api_reported_count")
    fetched_count = len(filings)

    if not filings or len(filings) == 0:
        print(f"⚠️  No filings retrieved for {year}")
        print("   This could be due to:")
        print("   - Network connectivity issues")
        print("   - Senate API being unavailable")
        print("   - No data for this year yet")
        _finish_ingestion_run(
            db_path,
            run_id,
            status="no_data",
            api_reported_count=api_reported,
            fetched_count=fetched_count,
            filtered_count=0,
            aggregate_rows=0,
            is_complete=False,
            notes=fetch_meta.get("error"),
        )
        return

    # If the fetch did not clearly reach the natural API end, refuse to overwrite
    # existing snapshots unless strict mode is disabled.
    fetch_is_complete = (
        bool(fetch_meta.get("reached_end"))
        and not bool(fetch_meta.get("hit_page_cap"))
        and not bool(fetch_meta.get("incomplete"))
    )
    if strict_fetch_completeness and not fetch_is_complete and not force_partial:
        print(
            f"⚠️  Fetch appears incomplete (did not reach natural API end).\n"
            f"   Fetched {fetched_count:,} filings so far.\n"
            f"   Aborting write to protect existing snapshot.\n"
            f"\n   Options:\n"
            f"     • Wait for the full fetch to complete and re-run.\n"
            f"     • Pass force_partial=True to write this partial snapshot anyway.\n"
            f"     • Set STRICT_FETCH_COMPLETENESS = False in config.py to disable globally."
        )
        _finish_ingestion_run(
            db_path,
            run_id,
            status="aborted_incomplete_fetch",
            api_reported_count=api_reported,
            fetched_count=fetched_count,
            filtered_count=0,
            aggregate_rows=0,
            is_complete=False,
            notes=fetch_meta.get("error") or "reached_end=false or cap/retry stop",
        )
        return

    print(f"✅ Retrieved {len(filings)} filings")

    # Convert to DataFrame
    filings_df = pd.DataFrame(filings)

    # Ensure we have the columns we need
    if "filing_year" not in filings_df.columns:
        filings_df["filing_year"] = year
    if "filing_type" not in filings_df.columns:
        filings_df["filing_type"] = None
    if "registrant_id" not in filings_df.columns:
        filings_df["registrant_id"] = None
    if "client_id" not in filings_df.columns:
        filings_df["client_id"] = None
    if "filing_period" in filings_df.columns:
        # Derive quarter from filing_period
        filings_df["quarter"] = filings_df["filing_period"].apply(quarter_from_period)
    else:
        filings_df["quarter"] = "Q1"  # Fallback

    # Rename columns to match DB schema
    if "filing_year" in filings_df.columns:
        filings_df = filings_df.rename(columns={"filing_year": "year"})
    if "filing_period" in filings_df.columns:
        filings_df = filings_df.rename(columns={"filing_period": "period"})

    # ------------------------------------------------------------------
    # 1b) Filter filing types and keep latest amendment/version only
    # ------------------------------------------------------------------
    before_filter_count = len(filings_df)
    filtered_by_type = filings_df[
        filings_df["filing_type"].apply(
            lambda ft: is_allowed_filing_type(ft, allowed_filing_types)
        )
    ].copy()
    after_type_filter_count = len(filtered_by_type)
    if before_filter_count > 0 and after_type_filter_count == 0:
        sample_types = (
            filings_df["filing_type"]
            .fillna("NULL")
            .astype(str)
            .value_counts()
            .head(8)
            .to_dict()
        )
        print(
            f"⚠️  Filing type filter matched 0/{before_filter_count:,} rows "
            f"(allowed types: {allowed_filing_types})."
        )
        print(f"   Observed filing_type values (sample): {sample_types}")
        print("   Falling back to unfiltered filings for this run.")
    else:
        filings_df = filtered_by_type
    print(
        f"✅ Filing type filter kept {len(filings_df):,}/{before_filter_count:,} rows "
        f"(allowed types: {allowed_filing_types})"
    )

    # Keep only filings with required dimensions.
    filings_df = filings_df[
        filings_df["client_name"].notna() & filings_df["period"].notna()
    ].copy()

    # Normalize amount and drop values below the configured minimum threshold.
    min_filing_amount = max(getattr(config, "MIN_FILING_AMOUNT", 0), 1)
    before_amount_filter = len(filings_df)
    filings_df["amount"] = pd.to_numeric(filings_df["amount"], errors="coerce").fillna(0.0)
    filings_df = filings_df[filings_df["amount"] >= min_filing_amount].copy()
    dropped_amount = before_amount_filter - len(filings_df)
    print(
        f"✅ Amount filter (>= ${min_filing_amount:,}): kept {len(filings_df):,}, "
        f"dropped {dropped_amount:,} zero/sub-threshold rows"
    )

    if keep_latest_only:
        dedupe_before = len(filings_df)
        # Cast to str after fillna to avoid pandas FutureWarning about object-dtype
        # downcast inference on mixed-type columns (e.g. int ID / str name).
        filings_df["registrant_key"] = (
            filings_df["registrant_id"]
            .fillna(filings_df["registrant_name"])
            .astype(str)
        )
        filings_df["client_key"] = (
            filings_df["client_id"]
            .fillna(filings_df["client_name"])
            .astype(str)
        )
        filings_df["filing_date_ts"] = pd.to_datetime(
            filings_df["filing_date"], errors="coerce", utc=True
        )
        filings_df = filings_df.sort_values("filing_date_ts").drop_duplicates(
            subset=["registrant_key", "client_key", "year", "period"], keep="last"
        )
        dedupe_after = len(filings_df)
        print(
            f"✅ Amendment/version dedupe kept {dedupe_after:,}/{dedupe_before:,} latest rows"
        )
        filings_df = filings_df.drop(
            columns=["registrant_key", "client_key", "filing_date_ts"], errors="ignore"
        )
    filtered_count = len(filings_df)

    # ------------------------------------------------------------------
    # 2) Prepare raw-year payload and aggregate in-memory first
    #     (so we never delete existing DB rows unless this build is valid)
    # ------------------------------------------------------------------
    rows_to_insert = []
    for _, row in filings_df.iterrows():
        rows_to_insert.append(
            (
                row.get("filing_uuid"),
                row.get("filing_type"),
                row.get("registrant_name"),
                row.get("registrant_id"),
                row.get("client_name"),
                row.get("client_id"),
                float(row.get("amount", 0) or 0),
                int(row.get("year", year)),
                row.get("period") or row.get("quarter") or "",
                row.get("filing_date") or "",
                datetime.now(timezone.utc).isoformat(),
            )
        )

    # ------------------------------------------------------------------
    # 3) Aggregate to company-year-quarter (pre-write validation stage)
    # ------------------------------------------------------------------
    print("\n📊 Aggregating filings by company/year/quarter...")
    agg_df = (
        filings_df.groupby(["client_name", "year", "period"], dropna=False, as_index=False)
        .agg(
            total_lobbying_spend=("amount", "sum"),
            num_filings=("amount", "size"),
        )
        .rename(columns={"client_name": "company_name"})
    )
    agg_df = agg_df[agg_df["company_name"].notna() & agg_df["period"].notna()].copy()

    if agg_df.empty:
        print(f"⚠️  No aggregated data for {year}")
        _finish_ingestion_run(
            db_path,
            run_id,
            status="failed_no_aggregate",
            api_reported_count=api_reported,
            fetched_count=fetched_count,
            filtered_count=filtered_count,
            aggregate_rows=0,
            is_complete=False,
        )
        return

    print(f"✅ Aggregated into {len(agg_df)} company-quarter records")
    aggregate_rows = len(agg_df)

    # Normalize quarter labels
    agg_df["quarter"] = agg_df["period"].apply(quarter_from_period)
    invalid_quarter_rows = agg_df["quarter"].isna().sum()
    if invalid_quarter_rows:
        print(
            f"⚠️  Dropping {invalid_quarter_rows} rows with unknown filing period labels"
        )
        agg_df = agg_df[agg_df["quarter"].notna()].copy()

    # ------------------------------------------------------------------
    # 4) Enrich with ticker, market cap, sector, spend_to_mcap_ratio
    # ------------------------------------------------------------------
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    enriched_rows = []
    print("\n🔍 Enriching with tickers & market caps...")
    print("   (This may take a while for many companies...)\n")

    success_count = 0
    failed_count = 0

    for idx, row in agg_df.iterrows():
        company_name = row["company_name"]
        qtr = row["quarter"]
        total_spend = float(row["total_lobbying_spend"] or 0)

        # Map company to ticker
        ticker, match_method = mapper.find_ticker_with_info(company_name)
        market_cap = None
        sector = None
        spend_to_mcap = None

        if ticker:
            # Fetch market cap + sector together (both cached in ticker_market_caps)
            ticker_info = get_or_fetch_ticker_info(conn, ticker)
            market_cap = ticker_info["market_cap"]
            sector = ticker_info["sector"]

            if market_cap and market_cap > 0:
                spend_to_mcap = total_spend / market_cap
                success_count += 1
                if (idx + 1) % 50 == 0:
                    print(f"   Processed {idx + 1}/{len(agg_df)} companies...")
            else:
                failed_count += 1
        else:
            failed_count += 1

        # Store even if ticker/mcap missing (you can filter later)
        if public_company_only and not ticker:
            continue

        enriched_rows.append(
            (
                company_name,
                ticker,
                year,
                qtr,
                total_spend,
                market_cap,
                spend_to_mcap,
                sector,
                match_method,
                datetime.now(timezone.utc).isoformat(),
            )
        )

    print("\n✅ Enrichment complete:")
    print(f"   Successfully matched: {success_count} companies")
    print(f"   Failed to match: {failed_count} companies")
    if public_company_only:
        print(f"   Public-company-only mode: {len(enriched_rows)} rows kept")

    # ------------------------------------------------------------------
    # 5) Persist raw + aggregate snapshots atomically for this year
    # ------------------------------------------------------------------
    print("\n💾 Writing yearly snapshot to database (atomic transaction)...")
    inserted_raw = 0
    try:
        # Ensure match_method column exists (legacy DB compatibility)
        cursor.execute("PRAGMA table_info(company_lobbying)")
        cols = {row[1] for row in cursor.fetchall()}
        if "match_method" not in cols:
            cursor.execute("ALTER TABLE company_lobbying ADD COLUMN match_method TEXT")

        cursor.execute("BEGIN")

        cursor.execute("DELETE FROM lobbying_filings WHERE year = ?", (year,))
        before_insert_changes = conn.total_changes
        cursor.executemany(
            """
            INSERT OR IGNORE INTO lobbying_filings
            (filing_uuid, filing_type, registrant_name, registrant_id, client_name, client_id,
             amount, year, period, filing_date, fetched_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        inserted_raw = conn.total_changes - before_insert_changes

        cursor.execute("DELETE FROM company_lobbying WHERE year = ?", (year,))
        cursor.executemany(
            """
            INSERT OR REPLACE INTO company_lobbying
            (company_name, ticker, year, quarter, total_lobbying_spend,
             market_cap, spend_to_mcap_ratio, sector, match_method, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            enriched_rows,
        )

        conn.commit()
    except Exception as e:
        conn.rollback()
        _finish_ingestion_run(
            db_path,
            run_id,
            status="failed_persist",
            api_reported_count=api_reported,
            fetched_count=fetched_count,
            filtered_count=filtered_count,
            inserted_raw_count=inserted_raw,
            aggregate_rows=aggregate_rows,
            is_complete=False,
            notes=str(e),
        )
        raise RuntimeError(
            f"Failed to persist {year} snapshot atomically: {e}"
        ) from e
    finally:
        conn.close()

    print(f"✅ Inserted {inserted_raw:,} raw filings (duplicates ignored)")

    print(f"\n{'=' * 70}")
    print(f"✅ Successfully built company_lobbying for {year}")
    print(f"   Total companies: {len(enriched_rows)}")
    print(f"   Database updated: {db_path}")
    print(f"{'=' * 70}\n")
    print("🎉 You can now view this data in the Streamlit app!")
    print("   Run: streamlit run app.py")
    final_complete = bool(fetch_is_complete)
    _finish_ingestion_run(
        db_path,
        run_id,
        status="complete" if final_complete else "partial_success",
        api_reported_count=api_reported,
        fetched_count=fetched_count,
        filtered_count=filtered_count,
        inserted_raw_count=inserted_raw,
        aggregate_rows=aggregate_rows,
        is_complete=final_complete,
        notes="complete" if final_complete else "written_from_incomplete_fetch",
    )


def backfill_ticker_mappings(db_path: str = None, dry_run: bool = False) -> dict:
    """
    Scan company_lobbying for rows where ticker IS NULL, run them through
    CompanyMapper (with fuzzy matching + token-overlap guard), and update
    the DB in place.  Also fetches market_cap for newly matched tickers and
    records the match_method used for auditing.

    Args:
        db_path: Path to SQLite DB. Defaults to config.DATABASE_PATH.
        dry_run: If True, report what would change without writing.

    Returns:
        dict with keys: checked, matched, already_had_ticker, skipped_private
    """
    if db_path is None:
        db_path = config.DATABASE_PATH

    if getattr(config, "SEC_UNIVERSE_AUTO_SYNC", True) and not dry_run:
        try:
            sync_sec_ticker_universe(db_path=db_path, force=False)
        except Exception as exc:
            print(f"⚠️  SEC universe sync skipped due to error: {exc}")

    mapper = CompanyMapper(db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        # Ensure match_method column exists (idempotent)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(company_lobbying)")
        cols = {row[1] for row in cursor.fetchall()}
        if "match_method" not in cols:
            cursor.execute("ALTER TABLE company_lobbying ADD COLUMN match_method TEXT")
            conn.commit()

        unmatched = pd.read_sql_query(
            "SELECT DISTINCT company_name FROM company_lobbying WHERE ticker IS NULL OR ticker = ''",
            conn,
        )

        stats = {
            "checked": len(unmatched),
            "matched": 0,
            "already_had_ticker": 0,
            "skipped_private": 0,
            "method_counts": {},
        }

        print(f"\n{'=' * 70}")
        print(f"Ticker Backfill — {len(unmatched):,} unmatched company names")
        if dry_run:
            print("DRY RUN — no changes will be written")
        print(f"{'=' * 70}\n")

        updates = []  # (ticker, method, market_cap, company_name)
        method_counts = {}
        for _, row in unmatched.iterrows():
            company_name = row["company_name"]
            ticker, method = mapper.find_ticker_with_info(company_name)

            if ticker is None:
                continue  # still unmatched or explicitly private

            market_cap = get_or_fetch_market_cap(conn, ticker) if not dry_run else None
            updates.append((ticker, method, market_cap, company_name))
            stats["matched"] += 1
            method_key = method or "unknown"
            method_counts[method_key] = method_counts.get(method_key, 0) + 1

            if dry_run:
                print(f"  Would map [{method}]: {company_name!r}  →  {ticker}")

        if not dry_run and updates:
            cursor = conn.cursor()
            cursor.executemany(
                """
                UPDATE company_lobbying
                SET ticker       = ?,
                    match_method = ?,
                    market_cap   = ?,
                    spend_to_mcap_ratio = CASE
                        WHEN ? IS NOT NULL AND ? > 0
                        THEN total_lobbying_spend / ?
                        ELSE spend_to_mcap_ratio
                    END,
                    last_updated = ?
                WHERE company_name = ?
                """,
                [
                    (ticker, method, mcap, mcap, mcap, mcap,
                     datetime.now(timezone.utc).isoformat(), company)
                    for (ticker, method, mcap, company) in updates
                ],
            )
            conn.commit()
            print(f"✅ Updated {cursor.rowcount:,} rows across {stats['matched']} companies")
    finally:
        conn.close()

    print(f"\nBackfill summary:")
    print(f"  Unmatched rows checked : {stats['checked']:,}")
    print(f"  Newly matched          : {stats['matched']:,}")
    if method_counts:
        print("  Match methods used     :")
        for method, count in sorted(method_counts.items(), key=lambda x: (-x[1], x[0])):
            print(f"    - {method:<14} {count:,}")
    print(f"{'=' * 70}\n")
    stats["method_counts"] = method_counts
    return stats


def revalidate_ticker_mappings(db_path: str = None, dry_run: bool = False) -> dict:
    """
    Re-evaluate ALL existing ticker assignments in company_lobbying against
    the current (stricter) CompanyMapper logic.

    - Rows whose ticker would now map to *nothing* are NULLIFIED.
    - Rows whose ticker would now map to a *different* value are REMAPPED.
    - Rows that still map to the same ticker are left untouched.

    This purges stale assignments from the old 0.72-era fuzzy matching
    (e.g. BP AMERICA → BAC, DJI TECHNOLOGY → MU).

    Args:
        db_path: Path to SQLite DB.
        dry_run: If True, report proposed changes without writing.

    Returns:
        dict with keys: checked, nullified, changed, unchanged
    """
    if db_path is None:
        db_path = config.DATABASE_PATH

    if getattr(config, "SEC_UNIVERSE_AUTO_SYNC", True) and not dry_run:
        try:
            sync_sec_ticker_universe(db_path=db_path, force=False)
        except Exception as exc:
            print(f"⚠️  SEC universe sync skipped due to error: {exc}")

    mapper = CompanyMapper(db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        # Ensure match_method column exists
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(company_lobbying)")
        cols = {row[1] for row in cursor.fetchall()}
        if "match_method" not in cols:
            cursor.execute("ALTER TABLE company_lobbying ADD COLUMN match_method TEXT")
            conn.commit()

        existing = pd.read_sql_query(
            """
            SELECT DISTINCT company_name, ticker
            FROM company_lobbying
            WHERE ticker IS NOT NULL AND ticker != ''
            """,
            conn,
        )

        stats = {"checked": len(existing), "nullified": 0, "changed": 0, "unchanged": 0}
        remap_method_counts = {}
        to_nullify: list[str] = []
        to_change: list[tuple] = []   # (new_ticker, method, market_cap, company_name)

        print(f"\n{'=' * 70}")
        print(f"Ticker Revalidation — {len(existing):,} distinct company→ticker pairs")
        if dry_run:
            print("DRY RUN — no changes will be written")
        print(f"{'=' * 70}\n")

        for _, row in existing.iterrows():
            company_name = row["company_name"]
            current_ticker = str(row["ticker"]).strip()

            new_ticker, method = mapper.find_ticker_with_info(company_name)

            if new_ticker is None:
                to_nullify.append(company_name)
                stats["nullified"] += 1
                if dry_run:
                    print(f"  NULLIFY [{current_ticker}]: {company_name!r}")
            elif new_ticker != current_ticker:
                new_mcap = None if dry_run else get_or_fetch_market_cap(conn, new_ticker)
                to_change.append((new_ticker, method, new_mcap, company_name))
                stats["changed"] += 1
                method_key = method or "unknown"
                remap_method_counts[method_key] = remap_method_counts.get(method_key, 0) + 1
                if dry_run:
                    print(
                        f"  REMAP   [{current_ticker} → {new_ticker}] "
                        f"({method}): {company_name!r}"
                    )
            else:
                stats["unchanged"] += 1

        if not dry_run:
            now_iso = datetime.now(timezone.utc).isoformat()
            cursor = conn.cursor()

            if to_nullify:
                cursor.executemany(
                    """
                    UPDATE company_lobbying
                    SET ticker              = NULL,
                        market_cap          = NULL,
                        spend_to_mcap_ratio = NULL,
                        match_method        = 'invalidated',
                        last_updated        = ?
                    WHERE company_name = ?
                    """,
                    [(now_iso, name) for name in to_nullify],
                )
                conn.commit()
                print(f"✅ Nullified {stats['nullified']:,} bad ticker assignments")

            if to_change:
                cursor.executemany(
                    """
                    UPDATE company_lobbying
                    SET ticker       = ?,
                        match_method = ?,
                        market_cap   = ?,
                        spend_to_mcap_ratio = CASE
                            WHEN ? IS NOT NULL AND ? > 0
                            THEN total_lobbying_spend / ?
                            ELSE NULL
                        END,
                        last_updated = ?
                    WHERE company_name = ?
                    """,
                    [
                        (t, m, mc, mc, mc, mc, now_iso, c)
                        for t, m, mc, c in to_change
                    ],
                )
                conn.commit()
                print(f"✅ Remapped {stats['changed']:,} tickers to correct values")
    finally:
        conn.close()

    print(f"\nRevalidation summary:")
    print(f"  Checked   : {stats['checked']:,}")
    print(f"  Nullified : {stats['nullified']:,}  ← stale/wrong matches purged")
    print(f"  Remapped  : {stats['changed']:,}  ← corrected to different ticker")
    if remap_method_counts:
        print("  Remap methods:")
        for method, count in sorted(remap_method_counts.items(), key=lambda x: (-x[1], x[0])):
            print(f"    - {method:<14} {count:,}")
    print(f"  Unchanged : {stats['unchanged']:,}  ← already correct")
    print(f"{'=' * 70}\n")
    stats["remap_method_counts"] = remap_method_counts
    return stats


def populate_stock_performance(db_path: str = None, lookback_years: int = 3) -> dict:
    """
    For every distinct ticker in company_lobbying, fetch historical price data
    and calculate forward returns (1m, 3m, 6m, 1y) anchored to a lag-adjusted
    filing signal date for each quarter.
    Results are stored in the stock_performance table.

    Args:
        db_path: Path to SQLite DB.
        lookback_years: How many years of data to process.

    Returns:
        dict with keys: tickers_processed, rows_inserted, errors
    """
    if db_path is None:
        db_path = config.DATABASE_PATH

    yfinance_price_enabled = bool(
        getattr(
            config,
            "YFINANCE_PRICE_HISTORY_ENABLED",
            getattr(config, "YFINANCE_ENABLED", True),
        )
    )
    if not yfinance_price_enabled:
        print(
            "Stock performance population skipped: "
            "YFINANCE_PRICE_HISTORY_ENABLED is False."
        )
        return {
            "tickers_processed": 0,
            "rows_inserted": 0,
            "errors": 0,
            "skipped_stale_reference": 0,
        }

    conn = sqlite3.connect(db_path)
    try:
        return _populate_stock_performance_inner(conn, db_path, lookback_years)
    finally:
        conn.close()


def _populate_stock_performance_inner(conn, db_path, lookback_years):
    import yfinance as yf

    # Quarter-end reference dates
    quarter_ends = {
        "Q1": "-03-31",
        "Q2": "-06-30",
        "Q3": "-09-30",
        "Q4": "-12-31",
    }

    current_year = datetime.now().year
    min_year = current_year - lookback_years

    # Get all ticker+year+quarter combos we care about
    combos = pd.read_sql_query(
        """
        SELECT DISTINCT ticker, year, quarter
        FROM company_lobbying
        WHERE ticker IS NOT NULL
          AND ticker != ''
          AND year >= ?
        ORDER BY ticker, year, quarter
        """,
        conn,
        params=(min_year,),
    )

    if combos.empty:
        print("No matched tickers found in company_lobbying — run backfill first.")
        return {"tickers_processed": 0, "rows_inserted": 0, "errors": 0}

    tickers = combos["ticker"].unique().tolist()
    print(f"\n{'=' * 70}")
    print(f"Stock Performance Population")
    print(f"  Tickers to process: {len(tickers)}")
    print(f"  Fetching price history from yfinance...")
    print(f"{'=' * 70}\n")

    max_ref_gap_days = int(getattr(config, "STOCK_REF_MAX_GAP_DAYS", 10))
    filing_lag_days = max(int(getattr(config, "LDA_FILING_LAG_DAYS", 20) or 0), 0)
    stats = {
        "tickers_processed": 0,
        "rows_inserted": 0,
        "errors": 0,
        "skipped_stale_reference": 0,
    }
    cursor = conn.cursor()

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=f"{lookback_years + 2}y", auto_adjust=True)

            if hist.empty:
                stats["errors"] += 1
                continue

            hist.index = hist.index.tz_localize(None)  # remove timezone

            # Process each quarter for this ticker
            ticker_combos = combos[combos["ticker"] == ticker]
            rows_for_ticker = []

            for _, combo in ticker_combos.iterrows():
                yr = int(combo["year"])
                qtr = combo["quarter"]

                if qtr not in quarter_ends:
                    continue

                quarter_end_str = f"{yr}{quarter_ends[qtr]}"
                try:
                    quarter_end_dt = pd.to_datetime(quarter_end_str)
                except Exception:
                    continue
                ref_date = quarter_end_dt + pd.Timedelta(days=filing_lag_days)

                # Find the closest trading day on or after the lag-adjusted
                # disclosure signal date.
                future_hist = hist[hist.index >= ref_date]
                if future_hist.empty:
                    continue

                ref_price = float(future_hist.iloc[0]["Close"])
                actual_ts = future_hist.index[0]
                ref_gap_days = int((actual_ts - ref_date).days)
                # Guardrail: only allow near-quarter-end snapshots.  This avoids
                # pre-IPO contamination where multiple historical quarters would
                # all snap to the first-ever trade date months later.
                if ref_gap_days > max_ref_gap_days:
                    stats["skipped_stale_reference"] += 1
                    continue
                actual_date = actual_ts.strftime("%Y-%m-%d")

                returns = {}
                for period, days in [("1m", 30), ("3m", 91), ("6m", 182), ("1y", 365)]:
                    target_date = ref_date + pd.Timedelta(days=days)
                    if target_date > pd.Timestamp.now():
                        continue
                    future_slice = hist[hist.index >= target_date]
                    if not future_slice.empty:
                        fwd_price = float(future_slice.iloc[0]["Close"])
                        returns[f"return_{period}"] = round(
                            (fwd_price - ref_price) / ref_price * 100, 4
                        )

                rows_for_ticker.append((
                    ticker,
                    actual_date,
                    round(ref_price, 4),
                    returns.get("return_1m"),
                    returns.get("return_3m"),
                    returns.get("return_6m"),
                    returns.get("return_1y"),
                ))

            if rows_for_ticker:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO stock_performance
                    (ticker, date, close_price, return_1m, return_3m, return_6m, return_1y)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_for_ticker,
                )
                conn.commit()
                stats["rows_inserted"] += len(rows_for_ticker)

            stats["tickers_processed"] += 1
            time.sleep(0.4)  # light rate limiting

        except Exception as e:
            print(f"  ❌ {ticker}: {e}")
            stats["errors"] += 1

    print(f"\n✅ Stock performance population complete:")
    print(f"   Tickers processed : {stats['tickers_processed']}")
    print(f"   Rows inserted     : {stats['rows_inserted']}")
    print(f"   Filing lag used    : {filing_lag_days} day(s)")
    print(f"   Skipped (stale ref): {stats['skipped_stale_reference']}")
    print(f"   Errors            : {stats['errors']}")
    print(f"{'=' * 70}\n")
    return stats


if __name__ == "__main__":
    # Example: build last 3 years
    current_year = datetime.now().year

    print("\n" + "=" * 70)
    print("US Lobbying Data Pipeline")
    print("Building company lobbying database")
    print("=" * 70)

    # You can customize years here
    years_to_build = [current_year - 2, current_year - 1, current_year]

    print(f"\nWill fetch data for years: {years_to_build}")
    print("This may take 10–30 minutes depending on data volume...\n")

    for yr in years_to_build:
        try:
            build_company_lobbying_for_year(yr)
        except Exception as e:
            print(f"\n❌ Error building data for {yr}: {e}")
            print("   Continuing with next year...\n")

    print("\n" + "=" * 70)
    print("Pipeline complete!")
    print("=" * 70)
