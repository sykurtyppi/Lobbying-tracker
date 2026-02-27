#!/usr/bin/env python3
"""
Initialize the lobbying database
Run this to set up the database structure
"""

import sqlite3
import os
from pathlib import Path

def init_database(db_path="data/lobbying_data.db"):
    """Initialize the SQLite database with required tables"""

    # Create data directory if it doesn't exist
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    print(f"Initializing database at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Enable WAL mode for better concurrent read performance
    cursor.execute("PRAGMA journal_mode=WAL")

    # ──────────────────────────────────────────────────────────────────────────
    # Core tables
    # ──────────────────────────────────────────────────────────────────────────

    # Lobbying filings table
    print("Creating lobbying_filings table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lobbying_filings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_uuid TEXT UNIQUE,
            filing_type TEXT,
            registrant_name TEXT,
            registrant_id TEXT,
            client_name TEXT,
            client_id TEXT,
            amount REAL,
            year INTEGER,
            period TEXT,
            filing_date TEXT,
            fetched_date TEXT,
            UNIQUE(registrant_name, client_name, year, period)
        )
    """)

    # Company aggregates table
    print("Creating company_lobbying table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_lobbying (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            ticker TEXT,
            year INTEGER,
            quarter TEXT,
            total_lobbying_spend REAL,
            market_cap REAL,
            spend_to_mcap_ratio REAL,
            sector TEXT,
            match_method TEXT,
            last_updated TEXT,
            UNIQUE(company_name, year, quarter)
        )
    """)

    # Stock performance tracking
    print("Creating stock_performance table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            date TEXT,
            close_price REAL,
            return_1m REAL,
            return_3m REAL,
            return_6m REAL,
            return_1y REAL,
            UNIQUE(ticker, date)
        )
    """)

    # ──────────────────────────────────────────────────────────────────────────
    # Supporting tables
    # ──────────────────────────────────────────────────────────────────────────

    # Cached market-cap + sector lookups (avoids repeated Yahoo Finance calls)
    print("Creating ticker_market_caps table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_market_caps (
            ticker TEXT PRIMARY KEY,
            market_cap REAL,
            sector TEXT,
            last_updated TEXT
        )
    """)

    # User-verified company→ticker alias overrides (survives re-fetches)
    print("Creating entity_aliases table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_name TEXT NOT NULL,
            canonical_name TEXT,
            ticker TEXT,
            status TEXT DEFAULT 'verified',
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(alias_name)
        )
    """)

    # Per-year ingestion run audit trail
    print("Creating ingestion_runs table...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            status TEXT,
            api_reported_count INTEGER,
            fetched_count INTEGER,
            filtered_count INTEGER,
            inserted_raw_count INTEGER,
            aggregate_rows INTEGER,
            is_complete INTEGER DEFAULT 0,
            notes TEXT
        )
    """)

    # SEC official ticker universe cache
    print("Creating sec_ticker_universe table...")
    cursor.execute("""
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
    """)

    # ──────────────────────────────────────────────────────────────────────────
    # Legacy-column migration: add match_method if upgrading an existing DB
    # ──────────────────────────────────────────────────────────────────────────
    cursor.execute("PRAGMA table_info(company_lobbying)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "match_method" not in existing_cols:
        print("  Adding match_method column to company_lobbying (upgrade)...")
        cursor.execute("ALTER TABLE company_lobbying ADD COLUMN match_method TEXT")

    cursor.execute("PRAGMA table_info(ticker_market_caps)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    if "sector" not in existing_cols:
        print("  Adding sector column to ticker_market_caps (upgrade)...")
        cursor.execute("ALTER TABLE ticker_market_caps ADD COLUMN sector TEXT")

    # ──────────────────────────────────────────────────────────────────────────
    # Indexes
    # ──────────────────────────────────────────────────────────────────────────
    print("Creating indexes...")

    # company_lobbying
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_lobbying_year_quarter "
        "ON company_lobbying(year, quarter)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_lobbying_ticker_year "
        "ON company_lobbying(ticker, year)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_lobbying_spend "
        "ON company_lobbying(total_lobbying_spend DESC)"
    )

    # lobbying_filings
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lobbying_filings_year_period "
        "ON lobbying_filings(year, period)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lobbying_filings_client_year "
        "ON lobbying_filings(client_name, year)"
    )

    # stock_performance
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_stock_performance_ticker_date "
        "ON stock_performance(ticker, date)"
    )

    # entity_aliases
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_aliases_ticker "
        "ON entity_aliases(ticker)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_aliases_status "
        "ON entity_aliases(status)"
    )

    # ingestion_runs
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_year "
        "ON ingestion_runs(year, started_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sec_ticker_universe_normalized "
        "ON sec_ticker_universe(normalized_name)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sec_ticker_universe_exchange "
        "ON sec_ticker_universe(exchange)"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # Sample data (skipped if the DB already has real rows)
    # ──────────────────────────────────────────────────────────────────────────
    cursor.execute("SELECT COUNT(*) FROM company_lobbying")
    if cursor.fetchone()[0] == 0:
        print("Inserting sample data (no existing rows found)...")

        import yfinance as yf

        sample_companies = [
            ('Amazon.com Inc', 'AMZN', 2024, 'Q4', 6800000, 'Consumer'),
            ('Meta Platforms Inc', 'META', 2024, 'Q4', 5500000, 'Technology'),
            ('Alphabet Inc', 'GOOGL', 2024, 'Q4', 5200000, 'Technology'),
            ('Microsoft Corporation', 'MSFT', 2024, 'Q4', 4900000, 'Technology'),
            ('Apple Inc', 'AAPL', 2024, 'Q4', 4300000, 'Technology'),
            ('JPMorgan Chase & Co', 'JPM', 2024, 'Q4', 4100000, 'Financials'),
            ('Bank of America Corporation', 'BAC', 2024, 'Q4', 3900000, 'Financials'),
            ('Pfizer Inc', 'PFE', 2024, 'Q4', 3700000, 'Healthcare'),
            ('Johnson & Johnson', 'JNJ', 2024, 'Q4', 3500000, 'Healthcare'),
            ('Comcast Corporation', 'CMCSA', 2024, 'Q4', 3200000, 'Communications'),
            ('AT&T Inc', 'T', 2024, 'Q4', 3000000, 'Communications'),
            ('Verizon Communications', 'VZ', 2024, 'Q4', 2900000, 'Communications'),
            ('Boeing Company', 'BA', 2024, 'Q4', 2700000, 'Industrials'),
            ('Lockheed Martin', 'LMT', 2024, 'Q4', 2500000, 'Industrials'),
            ('General Electric', 'GE', 2024, 'Q4', 2400000, 'Industrials'),
            ('Chevron Corporation', 'CVX', 2024, 'Q4', 2200000, 'Energy'),
            ('Exxon Mobil Corporation', 'XOM', 2024, 'Q4', 2100000, 'Energy'),
            ('UnitedHealth Group', 'UNH', 2024, 'Q4', 2000000, 'Healthcare'),
            ('CVS Health Corporation', 'CVS', 2024, 'Q4', 1900000, 'Healthcare'),
            ('Walmart Inc', 'WMT', 2024, 'Q4', 1800000, 'Consumer'),
        ]

        print("  Fetching market caps from Yahoo Finance...")
        for company_name, ticker, year, quarter, lobbying_spend, sector in sample_companies:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                market_cap = info.get('marketCap') or 0
                if market_cap == 0:
                    fallback = {'AAPL': 3e12, 'MSFT': 3e12, 'GOOGL': 2e12,
                                'AMZN': 2e12, 'META': 1.5e12, 'JPM': 5e11,
                                'JNJ': 4e11}
                    market_cap = fallback.get(ticker, 1e11)
                spend_to_mcap = lobbying_spend / market_cap if market_cap > 0 else 0
                cursor.execute("""
                    INSERT OR IGNORE INTO company_lobbying
                    (company_name, ticker, year, quarter, total_lobbying_spend,
                     market_cap, spend_to_mcap_ratio, sector, match_method, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', datetime('now'))
                """, (company_name, ticker, year, quarter, lobbying_spend,
                      market_cap, spend_to_mcap, sector))
                print(f"  ✓ {ticker}: MCap=${market_cap/1e9:.1f}B  Spend=${lobbying_spend/1e6:.1f}M")
            except Exception as e:
                print(f"  ⚠ {ticker}: {e} — using placeholder")
                cursor.execute("""
                    INSERT OR IGNORE INTO company_lobbying
                    (company_name, ticker, year, quarter, total_lobbying_spend,
                     market_cap, spend_to_mcap_ratio, sector, match_method, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', datetime('now'))
                """, (company_name, ticker, year, quarter, lobbying_spend,
                      1e11, lobbying_spend / 1e11, sector))
    else:
        print("  Skipping sample data — existing rows found.")

    conn.commit()

    # Verify
    cursor.execute("SELECT COUNT(*) FROM company_lobbying")
    count = cursor.fetchone()[0]
    conn.close()

    print(f"\n✅ Database initialized successfully!")
    print(f"   Location: {db_path}")
    print(f"   Tables: lobbying_filings, company_lobbying, stock_performance,")
    print(f"            ticker_market_caps, entity_aliases, ingestion_runs")
    print(f"   Records in company_lobbying: {count}")
    print(f"\nRun the application with:")
    print(f"   streamlit run app.py")


if __name__ == "__main__":
    init_database()
