"""
Lobbying Data Fetcher
Professional tool for fetching and caching corporate lobbying disclosure data
"""

import requests
import sqlite3
import pandas as pd
from datetime import datetime, timedelta, timezone
import time
import json
import re
import difflib
from typing import Dict, List, Optional
import yfinance as yf


def _safe_float(value) -> Optional[float]:
    """Convert a value to float, returning None if conversion fails."""
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


class LobbyingDataFetcher:
    def __init__(self, db_path: str = "data/lobbying_data.db"):
        self.db_path = db_path
        # Ensure data directory exists
        import os
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        self.init_database()
        
    def init_database(self):
        """Initialize SQLite database with required tables"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
        
            # Lobbying filings table
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
            self._ensure_column(cursor, "lobbying_filings", "filing_type", "TEXT")
            self._ensure_column(cursor, "lobbying_filings", "registrant_id", "TEXT")
            self._ensure_column(cursor, "lobbying_filings", "client_id", "TEXT")
            
            # Company aggregates table
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
                    last_updated TEXT,
                    UNIQUE(company_name, year, quarter)
                )
            """)
            
            # Stock performance tracking
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

            # Manual alias review + verified mappings source of truth
            cursor.execute("""
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
            """)

            # Ingestion observability / completeness tracking
            cursor.execute("""
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
            """)

            # SEC official ticker universe (exchange-listed issuers)
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

            # Query performance indexes for common filter paths
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_company_lobbying_year_quarter ON company_lobbying(year, quarter)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_company_lobbying_ticker_year ON company_lobbying(ticker, year)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_company_lobbying_spend ON company_lobbying(total_lobbying_spend DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_lobbying_filings_year_period ON lobbying_filings(year, period)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_lobbying_filings_client_year ON lobbying_filings(client_name, year)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_entity_aliases_status ON entity_aliases(status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_entity_aliases_ticker ON entity_aliases(ticker)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_year_started ON ingestion_runs(year, started_at DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sec_ticker_universe_normalized ON sec_ticker_universe(normalized_name)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sec_ticker_universe_exchange ON sec_ticker_universe(exchange)"
            )

            # match_method records how each ticker was resolved:
            # "exact" | "suffix" | "case_insensitive" | "normalized" | "prefix" | "fuzzy" | "manual" | "invalidated"
            self._ensure_column(cursor, "company_lobbying", "match_method", "TEXT")

            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Database initialization error: {e}")
            raise

    @staticmethod
    def _ensure_column(cursor: sqlite3.Cursor, table: str, column: str, coltype: str):
        """Add missing columns during lightweight schema evolution."""
        cursor.execute(f"PRAGMA table_info({table})")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        
    # ── OpenSecrets integration ───────────────────────────────────────────────

    _OPENSECRETS_BASE = "https://www.opensecrets.org/api/"

    def test_opensecrets_connection(self, api_key: str) -> tuple[bool, str]:
        """
        Validate an OpenSecrets API key with a lightweight probe call.
        Returns (success: bool, message: str).
        """
        try:
            r = requests.get(
                self._OPENSECRETS_BASE,
                params={
                    "method": "getOrgs",
                    "org": "Microsoft",
                    "apikey": api_key,
                    "output": "json",
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if "response" in data:
                return True, "API key valid — connection successful."
            # API sometimes wraps errors inside the JSON body
            err = data.get("error", data.get("message", str(data)))
            return False, f"API responded but returned an error: {err}"
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            return False, f"HTTP {code} — check your API key."
        except requests.ConnectionError:
            return False, "Could not reach opensecrets.org — check your internet connection."
        except Exception as e:
            return False, f"Unexpected error: {e}"

    def _opensecrets_org_lookup(self, api_key: str, company_name: str) -> Optional[str]:
        """
        Resolve a company name to its OpenSecrets CRPId via getOrgs.
        Returns the CRPId string or None on failure.
        """
        try:
            r = requests.get(
                self._OPENSECRETS_BASE,
                params={
                    "method": "getOrgs",
                    "org": company_name,
                    "apikey": api_key,
                    "output": "json",
                },
                timeout=10,
            )
            r.raise_for_status()
            orgs = (
                r.json()
                .get("response", {})
                .get("organizations", {})
                .get("organization", [])
            )
            if not orgs:
                return None
            if isinstance(orgs, dict):
                orgs = [orgs]
            return orgs[0].get("@attributes", {}).get("orgid") or None
        except Exception:
            return None

    def _opensecrets_org_summary(
        self, api_key: str, crp_id: str, cycle: str
    ) -> dict:
        """
        Fetch orgSummary for a given CRPId + election cycle (e.g. "2024").
        Returns a dict with keys: total_contribs, pacs, indivs, lobbying.
        """
        try:
            r = requests.get(
                self._OPENSECRETS_BASE,
                params={
                    "method": "orgSummary",
                    "id": crp_id,
                    "cycle": cycle,
                    "apikey": api_key,
                    "output": "json",
                },
                timeout=10,
            )
            r.raise_for_status()
            attrs = (
                r.json()
                .get("response", {})
                .get("organization", {})
                .get("@attributes", {})
            )
            return {
                "total_contribs": _safe_float(attrs.get("total")),
                "pacs":           _safe_float(attrs.get("pacs")),
                "indivs":         _safe_float(attrs.get("indivs")),
                "lobbying":       _safe_float(attrs.get("lobbying")),
                "outside_spend":  _safe_float(attrs.get("outside_spending")),
            }
        except Exception:
            return {}

    def fetch_opensecrets_data(
        self,
        year: int = None,
        api_key: str = None,
        max_companies: int = 100,
        progress_callback=None,
    ) -> pd.DataFrame:
        """
        Fetch OpenSecrets org summaries for the top lobbying companies in `year`.

        Two-step per company:
          1. getOrgs(company_name) → CRPId
          2. orgSummary(CRPId, cycle) → total_contribs / pacs / indivs / lobbying

        Results are stored in the `opensecrets_contribs` table and returned
        as a DataFrame.  Requires a valid API key (opensecrets.org/api/admin/).

        Args:
            year:            Lobbying year to enrich (defaults to current year).
            api_key:         OpenSecrets API key; falls back to config if omitted.
            max_companies:   Cap on how many companies to query (rate-limit safety).
            progress_callback: Optional callable(current, total, company_name).
        """
        if year is None:
            year = datetime.now().year

        # Resolve API key
        if not api_key:
            try:
                import sys, os as _os
                sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
                from config import OPENSECRETS_API_KEY, OPENSECRETS_ENABLED
                if not OPENSECRETS_ENABLED or not OPENSECRETS_API_KEY:
                    print("OpenSecrets integration is disabled or has no API key in config.")
                    return pd.DataFrame()
                api_key = OPENSECRETS_API_KEY
            except ImportError:
                pass

        if not api_key:
            print(
                "No OpenSecrets API key. "
                "Register at https://www.opensecrets.org/api/admin/ and set it in Settings."
            )
            return pd.DataFrame()

        # Election cycle for orgSummary (2-year cycles; map year → most recent even year)
        cycle = str(year if year % 2 == 0 else year + 1)

        # Companies to enrich (top N by lobbying spend for the year)
        conn = sqlite3.connect(self.db_path)
        try:
            companies_df = pd.read_sql_query(
                """
                SELECT company_name, ticker,
                       SUM(total_lobbying_spend) AS total_spend
                FROM company_lobbying
                WHERE year = ? AND ticker IS NOT NULL AND ticker != ''
                GROUP BY company_name, ticker
                ORDER BY total_spend DESC
                LIMIT ?
                """,
                conn,
                params=(year, max_companies),
            )
        finally:
            conn.close()

        if companies_df.empty:
            print(f"No ticker-matched companies found for {year}.")
            return pd.DataFrame()

        self._ensure_opensecrets_table()

        results = []
        total = len(companies_df)
        for i, row in enumerate(companies_df.itertuples(), start=1):
            if progress_callback:
                progress_callback(i, total, row.company_name)

            crp_id = self._opensecrets_org_lookup(api_key, row.company_name)
            time.sleep(0.25)  # ≤4 req/s to stay within limits

            summary = {}
            if crp_id:
                summary = self._opensecrets_org_summary(api_key, crp_id, cycle)
                time.sleep(0.25)

            results.append({
                "company_name":  row.company_name,
                "ticker":        row.ticker,
                "crp_id":        crp_id,
                "cycle":         cycle,
                "year":          year,
                "total_contribs": summary.get("total_contribs"),
                "pacs":          summary.get("pacs"),
                "indivs":        summary.get("indivs"),
                "os_lobbying":   summary.get("lobbying"),
                "outside_spend": summary.get("outside_spend"),
                "fetched_at":    datetime.now(timezone.utc).isoformat(),
            })

        df = pd.DataFrame(results)
        self._upsert_opensecrets_contribs(df)
        matched = df["crp_id"].notna().sum()
        print(
            f"OpenSecrets sync complete: {matched}/{total} companies matched, "
            f"{df['total_contribs'].notna().sum()} contribution records stored."
        )
        return df

    def _ensure_opensecrets_table(self) -> None:
        """Create opensecrets_contribs table if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS opensecrets_contribs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name  TEXT,
                    ticker        TEXT,
                    crp_id        TEXT,
                    cycle         TEXT,
                    year          INTEGER,
                    total_contribs REAL,
                    pacs          REAL,
                    indivs        REAL,
                    os_lobbying   REAL,
                    outside_spend REAL,
                    fetched_at    TEXT,
                    UNIQUE(company_name, year)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _upsert_opensecrets_contribs(self, df: pd.DataFrame) -> None:
        """Upsert OpenSecrets contribution records into the DB."""
        if df.empty:
            return
        conn = sqlite3.connect(self.db_path)
        try:
            for _, r in df.iterrows():
                conn.execute(
                    """
                    INSERT INTO opensecrets_contribs
                        (company_name, ticker, crp_id, cycle, year,
                         total_contribs, pacs, indivs, os_lobbying, outside_spend, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_name, year) DO UPDATE SET
                        ticker        = excluded.ticker,
                        crp_id        = excluded.crp_id,
                        cycle         = excluded.cycle,
                        total_contribs = excluded.total_contribs,
                        pacs          = excluded.pacs,
                        indivs        = excluded.indivs,
                        os_lobbying   = excluded.os_lobbying,
                        outside_spend = excluded.outside_spend,
                        fetched_at    = excluded.fetched_at
                    """,
                    (
                        r["company_name"], r["ticker"], r.get("crp_id"),
                        r["cycle"], r["year"],
                        r.get("total_contribs"), r.get("pacs"), r.get("indivs"),
                        r.get("os_lobbying"), r.get("outside_spend"), r.get("fetched_at"),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    
    def fetch_senate_lobbying_data(self, year: int = None, quarter: int = None) -> pd.DataFrame:
        """
        Fetch data from Senate Lobbying Disclosure database
        This is publicly available and doesn't require API keys
        """
        if year is None:
            year = datetime.now().year
        if quarter is None:
            quarter = (datetime.now().month - 1) // 3 + 1
            
        print(f"Fetching Senate lobbying data for Q{quarter} {year}...")
        
        # Senate lobbying disclosure XML/API endpoint
        # https://lda.senate.gov/api/v1/filings/
        
        # This would be the actual implementation
        # For now, returning sample structure
        data = []
        
        return pd.DataFrame(data)
    
    def scrape_lobbying_data_bulk(self, start_year: int = 2020) -> pd.DataFrame:
        """
        Bulk scrape lobbying data from public sources
        Uses multiple sources to build comprehensive dataset
        """
        all_data = []
        current_year = datetime.now().year
        
        for year in range(start_year, current_year + 1):
            print(f"Processing year {year}...")
            
            # Fetch from available sources
            # We'll implement actual scraping logic here
            
            time.sleep(1)  # Rate limiting
            
        return pd.DataFrame(all_data)
    
    def fetch_market_cap(self, ticker: str) -> Optional[float]:
        """Fetch current market cap for a ticker"""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return info.get('marketCap', None)
        except Exception as e:
            print(f"Error fetching market cap for {ticker}: {e}")
            return None
    
    def fetch_stock_prices(self, ticker: str, start_date: str = None) -> pd.DataFrame:
        """Fetch historical stock prices"""
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=365*2)).strftime('%Y-%m-%d')
            
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(start=start_date)
            return hist
        except Exception as e:
            print(f"Error fetching prices for {ticker}: {e}")
            return pd.DataFrame()
    
    def calculate_returns(self, ticker: str, reference_date: str) -> Dict[str, float]:
        """Calculate forward returns from a reference date"""
        try:
            ref_date = pd.to_datetime(reference_date)
            end_date = datetime.now()
            
            hist = self.fetch_stock_prices(ticker, start_date=(ref_date - timedelta(days=30)).strftime('%Y-%m-%d'))
            
            if hist.empty:
                return {}
            
            ref_price = hist.loc[hist.index >= ref_date].iloc[0]['Close'] if len(hist.loc[hist.index >= ref_date]) > 0 else None
            
            if ref_price is None:
                return {}
            
            returns = {}
            
            # Calculate returns for different periods
            for period, days in [('1m', 30), ('3m', 90), ('6m', 180), ('1y', 365)]:
                future_date = ref_date + timedelta(days=days)
                if future_date <= end_date:
                    future_prices = hist.loc[hist.index >= future_date]
                    if len(future_prices) > 0:
                        future_price = future_prices.iloc[0]['Close']
                        returns[f'return_{period}'] = ((future_price - ref_price) / ref_price) * 100
            
            return returns
            
        except Exception as e:
            print(f"Error calculating returns for {ticker}: {e}")
            return {}
    
    def save_to_database(self, data: pd.DataFrame, table: str):
        """Save dataframe to SQLite database"""
        conn = sqlite3.connect(self.db_path)
        data['last_updated'] = datetime.now().isoformat()
        data.to_sql(table, conn, if_exists='append', index=False)
        conn.close()
    
    def get_cached_data(self, table: str, filters: Dict = None) -> pd.DataFrame:
        """Retrieve cached data from database"""
        # Allowlist table names to prevent SQL injection via the table parameter
        _ALLOWED_TABLES = {
            'lobbying_filings', 'company_lobbying',
            'stock_performance', 'ticker_market_caps',
            'sec_ticker_universe',
        }
        if table not in _ALLOWED_TABLES:
            raise ValueError(
                f"Invalid table name '{table}'. Must be one of: {sorted(_ALLOWED_TABLES)}"
            )

        conn = sqlite3.connect(self.db_path)

        query = f"SELECT * FROM {table}"
        params: list = []

        if filters:
            # Validate each column name against a safe pattern before interpolating
            for col in filters.keys():
                if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', col):
                    raise ValueError(f"Invalid column name: '{col}'")
            conditions = [f"{col} = ?" for col in filters.keys()]
            query += " WHERE " + " AND ".join(conditions)
            params = list(filters.values())

        df = pd.read_sql_query(query, conn, params=params if params else None)
        conn.close()
        return df
    
    def aggregate_company_lobbying(self, company_name: str, year: int) -> Dict:
        """Aggregate lobbying spend by company and year"""
        conn = sqlite3.connect(self.db_path)
        
        query = """
            SELECT 
                client_name as company_name,
                year,
                SUM(amount) as total_spend,
                COUNT(*) as num_filings
            FROM lobbying_filings
            WHERE client_name = ? AND year = ?
            GROUP BY client_name, year
        """
        
        result = pd.read_sql_query(query, conn, params=(company_name, year))
        conn.close()
        
        return result.to_dict('records')[0] if not result.empty else {}
    
    def get_top_lobbyists(self, year: int, limit: int = 100) -> pd.DataFrame:
        """Get top lobbying spenders for a given year"""
        conn = sqlite3.connect(self.db_path)
        
        query = """
            SELECT 
                client_name,
                year,
                SUM(amount) as total_spend,
                COUNT(*) as num_filings
            FROM lobbying_filings
            WHERE year = ?
            GROUP BY client_name, year
            ORDER BY total_spend DESC
            LIMIT ?
        """
        
        df = pd.read_sql_query(query, conn, params=(year, limit))
        conn.close()
        return df


class CompanyMapper:
    """Maps company names to tickers and enriches with market data"""

    # Generic words that are common across unrelated firms; these should not
    # be enough to validate a fuzzy ticker match by themselves.
    GENERIC_FUZZY_TOKENS = {
        "THE",
        "TECHNOLOGY",
        "TECHNOLOGIES",
        "CLIENT",
        "SERVICES",
        "SERVICE",
        "SYSTEM",
        "SYSTEMS",
        "SOLUTIONS",
        "GLOBAL",
        "INTERNATIONAL",
        "AMERICA",
        "AMERICAN",
        "USA",
        "UNITED",
        "AUTOMOTIVE",
        "NATURAL",
        "RESOURCES",
        "INDUSTRY",
        "INDUSTRIES",
        "CHEMICAL",
        "CHEMICALS",
        "COMPANIES",
        "GROUP",
        "HOLDINGS",
        "COMMUNICATIONS",
        "ENERGY",
        "HEALTH",
        "HEALTHCARE",
        "PHARMACEUTICAL",
        "PHARMACEUTICALS",
        "THERAPEUTICS",
        "FINANCIAL",
        "FINANCIALS",
        "CAPITAL",
        "NETWORK",
        "NETWORKS",
        "DIGITAL",
        "CORPORATE",
        "MANAGEMENT",
        "PARTNERS",
        "PARTNERSHIP",
        "ENTERPRISE",
        "ENTERPRISES",
        "US",
        # "GENERAL" appears across unrelated companies (General Atomics, General
        # Electric, General Dynamics, General Mills …); it must not be the sole
        # evidence for a fuzzy match.
        "GENERAL",
        # Common descriptor words that don't disambiguate between firms:
        "NATIONAL",
        "FEDERAL",
        "ADVANCED",
        "INTEGRATED",
        "STRATEGIC",
    }
    
    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = db_path
        else:
            try:
                import config  # type: ignore

                self.db_path = getattr(config, "DATABASE_PATH", "data/lobbying_data.db")
            except Exception:
                self.db_path = "data/lobbying_data.db"

        self.ticker_map = self.load_ticker_mappings()
        self.normalized_ticker_map = self._build_normalized_lookup(self.ticker_map)
        self.sec_normalized_ticker_map = self._load_sec_normalized_lookup()
    
    def load_ticker_mappings(self) -> Dict[str, str]:
        """Load or create company name to ticker mappings"""
        # This would ideally load from a maintained CSV or API
        # For now, comprehensive mappings for major companies
        mappings = {
            # Tech Giants
            'Amazon.com Inc': 'AMZN',
            'Amazon.com': 'AMZN',
            'Amazon': 'AMZN',
            'Apple Inc': 'AAPL',
            'Apple': 'AAPL',
            'Meta Platforms Inc': 'META',
            'Meta Platforms': 'META',
            'Meta': 'META',
            'Facebook Inc': 'META',
            'Facebook': 'META',
            'Alphabet Inc': 'GOOGL',
            'Alphabet': 'GOOGL',
            'Google LLC': 'GOOGL',
            'Google': 'GOOGL',
            'Microsoft Corporation': 'MSFT',
            'Microsoft': 'MSFT',
            'Tesla Inc': 'TSLA',
            'Tesla': 'TSLA',
            'Netflix Inc': 'NFLX',
            'Netflix': 'NFLX',
            'NVIDIA Corporation': 'NVDA',
            'NVIDIA': 'NVDA',
            'Intel Corporation': 'INTC',
            'Intel': 'INTC',
            'AMD': 'AMD',
            'Advanced Micro Devices': 'AMD',
            'Oracle Corporation': 'ORCL',
            'Oracle': 'ORCL',
            'Salesforce Inc': 'CRM',
            'Salesforce': 'CRM',
            'Adobe Inc': 'ADBE',
            'Adobe': 'ADBE',
            'Cisco Systems': 'CSCO',
            'Cisco': 'CSCO',
            'IBM': 'IBM',
            'International Business Machines': 'IBM',
            
            # Financial Services
            'JPMorgan Chase & Co': 'JPM',
            'JPMorgan Chase': 'JPM',
            'JPMorgan': 'JPM',
            'Bank of America Corporation': 'BAC',
            'Bank of America': 'BAC',
            'Wells Fargo & Company': 'WFC',
            'Wells Fargo': 'WFC',
            'Citigroup Inc': 'C',
            'Citigroup': 'C',
            'Goldman Sachs Group Inc': 'GS',
            'Goldman Sachs': 'GS',
            'Morgan Stanley': 'MS',
            'American Express Company': 'AXP',
            'American Express': 'AXP',
            'Visa Inc': 'V',
            'Visa': 'V',
            'Mastercard Incorporated': 'MA',
            'Mastercard': 'MA',
            'PayPal Holdings Inc': 'PYPL',
            'PayPal': 'PYPL',
            
            # Healthcare & Pharma
            'Pfizer Inc': 'PFE',
            'Pfizer': 'PFE',
            'Johnson & Johnson': 'JNJ',
            'Johnson and Johnson': 'JNJ',
            'UnitedHealth Group': 'UNH',
            'UnitedHealth': 'UNH',
            'CVS Health Corporation': 'CVS',
            'CVS Health': 'CVS',
            'CVS': 'CVS',
            'Merck & Co Inc': 'MRK',
            'Merck': 'MRK',
            'AbbVie Inc': 'ABBV',
            'AbbVie': 'ABBV',
            'Abbott Laboratories': 'ABT',
            'Abbott': 'ABT',
            'Eli Lilly and Company': 'LLY',
            'Eli Lilly': 'LLY',
            'Bristol-Myers Squibb Company': 'BMY',
            'Bristol-Myers Squibb': 'BMY',
            'Amgen Inc': 'AMGN',
            'Amgen': 'AMGN',
            
            # Energy
            'Exxon Mobil Corporation': 'XOM',
            'Exxon Mobil': 'XOM',
            'ExxonMobil': 'XOM',
            'Chevron Corporation': 'CVX',
            'Chevron': 'CVX',
            'ConocoPhillips': 'COP',
            'Schlumberger NV': 'SLB',
            'Schlumberger': 'SLB',
            'Occidental Petroleum Corporation': 'OXY',
            'Occidental Petroleum': 'OXY',
            
            # Industrials & Defense
            'Boeing Company': 'BA',
            'Boeing': 'BA',
            'Lockheed Martin Corporation': 'LMT',
            'Lockheed Martin': 'LMT',
            'General Electric Company': 'GE',
            'General Electric': 'GE',
            'GE': 'GE',
            'Raytheon Technologies Corporation': 'RTX',
            'Raytheon': 'RTX',
            'Caterpillar Inc': 'CAT',
            'Caterpillar': 'CAT',
            '3M Company': 'MMM',
            '3M': 'MMM',
            'Honeywell International Inc': 'HON',
            'Honeywell': 'HON',
            'General Dynamics Corporation': 'GD',
            'General Dynamics': 'GD',
            'Northrop Grumman Corporation': 'NOC',
            'Northrop Grumman': 'NOC',
            
            # Communications
            'Comcast Corporation': 'CMCSA',
            'Comcast': 'CMCSA',
            'AT&T Inc': 'T',
            'AT&T': 'T',
            'Verizon Communications Inc': 'VZ',
            'Verizon': 'VZ',
            'T-Mobile US Inc': 'TMUS',
            'T-Mobile': 'TMUS',
            'Charter Communications Inc': 'CHTR',
            'Charter Communications': 'CHTR',
            'Walt Disney Company': 'DIS',
            'Disney': 'DIS',
            
            # Consumer & Retail
            'Walmart Inc': 'WMT',
            'Walmart': 'WMT',
            'Target Corporation': 'TGT',
            'Target': 'TGT',
            'Home Depot Inc': 'HD',
            'Home Depot': 'HD',
            'Costco Wholesale Corporation': 'COST',
            'Costco': 'COST',
            'Procter & Gamble Company': 'PG',
            'Procter & Gamble': 'PG',
            'Coca-Cola Company': 'KO',
            'Coca-Cola': 'KO',
            'PepsiCo Inc': 'PEP',
            'PepsiCo': 'PEP',
            'Nike Inc': 'NKE',
            'Nike': 'NKE',
            'McDonald\'s Corporation': 'MCD',
            'McDonald\'s': 'MCD',
            'Starbucks Corporation': 'SBUX',
            'Starbucks': 'SBUX',
            
            # Auto
            'General Motors Company': 'GM',
            'General Motors': 'GM',
            'GM': 'GM',
            'Ford Motor Company': 'F',
            'Ford': 'F',

            # Additional companies frequently appearing in Senate lobbying data
            # (sourced from unmatched_companies.csv analysis)
            'Mastercard Worldwide': 'MA',
            'MASTERCARD WORLDWIDE': 'MA',
            'RTX Corporation': 'RTX',
            'RTX Corporation and Affiliates': 'RTX',
            'RTX Corporation (FKA Raytheon Technologies Corporation)': 'RTX',
            'Fluor Corporation': 'FLR',
            'Fluor': 'FLR',
            'Cheniere Energy Inc': 'LNG',
            'Cheniere Energy': 'LNG',
            'Siemens Corporation': 'SIEGY',
            'Siemens': 'SIEGY',
            'Novartis': 'NVS',
            'Novartis AG': 'NVS',
            'Capital One Financial Corporation': 'COF',
            'Capital One Financial': 'COF',
            'Capital One': 'COF',
            'Rivian Automotive': 'RIVN',
            'Rivian Automotive LLC': 'RIVN',
            'Rocket Lab USA': 'RKLB',
            'Rocket Lab': 'RKLB',
            'National Fuel Gas Company': 'NFG',
            'National Fuel Gas': 'NFG',
            "Moody's Corporation": 'MCO',
            "Moody's": 'MCO',
            'Liberty Mutual Group': None,   # private
            'BorgWarner Inc': 'BWA',
            'BorgWarner': 'BWA',
            'Anheuser-Busch Companies Inc': 'BUD',
            'Anheuser-Busch': 'BUD',
            'Anheuser Busch': 'BUD',
            'PTC Therapeutics': 'PTCT',
            'PTC Therapeutics Inc': 'PTCT',
            'Applied Intuition': None,      # private
            'Sallie Mae Bank': 'SLM',
            'Sallie Mae': 'SLM',
            'SLM Corporation': 'SLM',
            # Pharma/biotech additions
            'AstraZeneca': 'AZN',
            'AstraZeneca PLC': 'AZN',
            'Bayer': 'BAYRY',
            'Bayer AG': 'BAYRY',
            'Roche': 'RHHBY',
            'Roche Holdings': 'RHHBY',
            'GlaxoSmithKline': 'GSK',
            'GSK': 'GSK',
            'Sanofi': 'SNY',
            'Novo Nordisk': 'NVO',
            'Biogen Inc': 'BIIB',
            'Biogen': 'BIIB',
            'Regeneron Pharmaceuticals': 'REGN',
            'Regeneron': 'REGN',
            'Gilead Sciences': 'GILD',
            'Gilead': 'GILD',
            'Moderna Inc': 'MRNA',
            'Moderna': 'MRNA',
            # Finance additions
            'BlackRock Inc': 'BLK',
            'BlackRock': 'BLK',
            'Fidelity Investments': None,   # private
            'Charles Schwab Corporation': 'SCHW',
            'Charles Schwab': 'SCHW',
            'Vanguard Group': None,          # private
            'Intercontinental Exchange': 'ICE',
            'CME Group': 'CME',
            'Nasdaq': 'NDAQ',
            'Nasdaq Inc': 'NDAQ',
            'S&P Global': 'SPGI',
            'S&P Global Inc': 'SPGI',
            'MSCI Inc': 'MSCI',
            'Fiserv Inc': 'FI',
            'Fiserv': 'FI',
            # Tech additions
            'Qualcomm': 'QCOM',
            'Qualcomm Incorporated': 'QCOM',
            'Texas Instruments': 'TXN',
            'Texas Instruments Incorporated': 'TXN',
            'Applied Materials': 'AMAT',
            'Applied Materials Inc': 'AMAT',
            'Lam Research': 'LRCX',
            'KLA Corporation': 'KLAC',
            'Micron Technology': 'MU',
            'Micron Technology Inc': 'MU',
            'Western Digital': 'WDC',
            'Seagate Technology': 'STX',
            'NetApp': 'NTAP',
            'Workday': 'WDAY',
            'ServiceNow': 'NOW',
            'Snowflake': 'SNOW',
            'Palantir Technologies': 'PLTR',
            'Palantir': 'PLTR',
            'CrowdStrike': 'CRWD',
            'Palo Alto Networks': 'PANW',
            'Fortinet': 'FTNT',
            'Cloudflare': 'NET',
            'Datadog': 'DDOG',
            'Twilio': 'TWLO',
            'Zoom Video Communications': 'ZM',
            'Zoom': 'ZM',
            # Energy additions
            'Pioneer Natural Resources': 'PXD',
            'Devon Energy': 'DVN',
            'EOG Resources': 'EOG',
            'Marathon Oil': 'MRO',
            'Marathon Petroleum': 'MPC',
            'Phillips 66': 'PSX',
            'Valero Energy': 'VLO',
            'Halliburton': 'HAL',
            'Baker Hughes': 'BKR',
            'Kinder Morgan': 'KMI',
            'Williams Companies': 'WMB',
            'Dominion Energy': 'D',
            'Duke Energy': 'DUK',
            'Southern Company': 'SO',
            'NextEra Energy': 'NEE',
            'Exelon': 'EXC',
            # Industrials/Defense additions
            'L3Harris Technologies': 'LHX',
            'L3 Technologies': 'LHX',
            'Harris Corporation': 'LHX',
            'Textron': 'TXT',
            'TransDigm Group': 'TDG',
            'Parker Hannifin': 'PH',
            'Emerson Electric': 'EMR',
            'Eaton Corporation': 'ETN',
            'Illinois Tool Works': 'ITW',
            'Deere & Company': 'DE',
            'John Deere': 'DE',
            'AGCO Corporation': 'AGCO',
            'United Parcel Service': 'UPS',
            'FedEx Corporation': 'FDX',
            'FedEx': 'FDX',
            # Healthcare additions
            'Medtronic': 'MDT',
            'Medtronic PLC': 'MDT',
            'Stryker': 'SYK',
            'Becton Dickinson': 'BDX',
            'Boston Scientific': 'BSX',
            'Edwards Lifesciences': 'EW',
            'Zimmer Biomet': 'ZBH',
            'Intuitive Surgical': 'ISRG',
            'Anthem': 'ELV',
            'Elevance Health': 'ELV',
            'Cigna': 'CI',
            'Humana': 'HUM',
            'Centene Corporation': 'CNC',
            'Molina Healthcare': 'MOH',
            'McKesson Corporation': 'MCK',
            'AmerisourceBergen': 'ABC',
            'Cardinal Health': 'CAH',
            'DaVita': 'DVA',
            # Consumer additions
            'Altria Group': 'MO',
            'Altria': 'MO',
            'Altria Client Services': 'MO',
            'Altria Client Services LLC': 'MO',
            'Philip Morris International': 'PM',
            'PMI US Corporate Services': 'PM',
            'PMI US Corporate Services Inc': 'PM',
            'PMI Global Services': 'PM',
            'PMI Global Services Inc': 'PM',
            'Reynolds American': 'BTI',
            'Colgate-Palmolive': 'CL',
            'Kimberly-Clark': 'KMB',
            'Church & Dwight': 'CHD',
            'Estee Lauder': 'EL',
            'Clorox': 'CLX',
            'Mondelez International': 'MDLZ',
            'Kraft Heinz': 'KHC',
            'General Mills': 'GIS',
            'Kellogg': 'K',
            "Kellog's": 'K',
            'WK Kellogg Co': 'KLG',
            'WK Kellogg Co.': 'KLG',
            'WK Kellogg Company': 'KLG',
            'WK Kellogg': 'KLG',
            'Conagra Brands': 'CAG',
            'Tyson Foods': 'TSN',
            'Sysco': 'SYY',
            "McDonald's": 'MCD',
            'Yum Brands': 'YUM',
            'Restaurant Brands International': 'QSR',
            'Darden Restaurants': 'DRI',
            'Dollar General': 'DG',
            'Dollar Tree': 'DLTR',
            'TJX Companies': 'TJX',
            'Ross Stores': 'ROST',
            # High-impact alias variants from lobbying disclosures
            'Google Client Services': 'GOOGL',
            'Google Client Services LLC': 'GOOGL',
            'The Cigna Group': 'CI',
            'Cigna Group': 'CI',
            'Chevron USA': 'CVX',
            'Chevron U.S.A.': 'CVX',
            'Chevron U.S.A. Inc': 'CVX',
            'Toyota Motor North America': 'TM',
            'Toyota Motor North America Inc': 'TM',
            'American Electric Power': 'AEP',
            'American Electric Power Company': 'AEP',
            'American Electric Power Company Inc': 'AEP',
            'Shell': 'SHEL',
            'Shell USA': 'SHEL',
            'Shell USA Inc': 'SHEL',
            'Shell Oil Company': 'SHEL',
            'Fresenius Medical Care North America': 'FMS',
            'Bayer Corporation': 'BAYRY',
            'Core Natural Resources': 'CNR',
            'Core Natural Resources Inc': 'CNR',
            'Core Natural Resources, Inc.': 'CNR',
            # High-spend alias variants observed in unmatched diagnostics
            'UPS (United Parcel Service)': 'UPS',
            'United Parcel Service, Inc.': 'UPS',
            'American Airlines Inc': 'AAL',
            'Aflac Incorporated': 'AFL',
            'Dow Chemical Company': 'DOW',
            'Dow Chemical Company DBA Dow': 'DOW',
            'Fox Corporation': 'FOXA',
            'Huntington Ingalls Industries Incorporated': 'HII',
            'Sanofi US Services Inc': 'SNY',
            'Sanofi U.S. Services, Inc.': 'SNY',
            'Citigroup Washington, Inc.': 'C',
        }

        # Merge in user-maintained overrides from config.py if available.
        try:
            import config  # type: ignore

            custom_mappings = getattr(config, "CUSTOM_TICKER_MAPPINGS", {})
            if isinstance(custom_mappings, dict):
                mappings.update(custom_mappings)
        except Exception:
            pass

        # Merge in verified aliases from DB (highest-precedence runtime overrides).
        # This powers the manual alias review/import workflow in the dashboard.
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                alias_df = pd.read_sql_query(
                    """
                    SELECT alias_name, ticker
                    FROM entity_aliases
                    WHERE ticker IS NOT NULL
                      AND ticker != ''
                      AND LOWER(COALESCE(status, 'verified')) IN ('verified', 'approved', 'active')
                    """,
                    conn,
                )
            finally:
                conn.close()

            for _, row in alias_df.iterrows():
                alias_name = str(row.get("alias_name", "")).strip()
                ticker = str(row.get("ticker", "")).strip().upper()
                if alias_name and ticker:
                    mappings[alias_name] = ticker
        except Exception:
            pass

        return mappings

    @staticmethod
    def _normalize_company_name(company_name: str) -> str:
        """Normalize issuer names to improve match quality across noisy variants."""
        if not company_name:
            return ""

        normalized = company_name.upper().strip()
        normalized = re.sub(r"\(.*?\)", " ", normalized)
        normalized = re.sub(
            r"\b(FORMERLY KNOWN AS|FORMERLY|FKA|DBA|D/B/A|A/K/A|AKA|N/K/A)\b.*",
            " ",
            normalized,
        )
        normalized = normalized.replace("&", " AND ")
        normalized = re.sub(r"[^A-Z0-9 ]", " ", normalized)
        normalized = re.sub(r"^THE\s+", "", normalized)
        normalized = re.sub(r"\bAND VARIOUS SUBSIDIARIES\b", " ", normalized)
        normalized = re.sub(r"\bVARIOUS SUBSIDIARIES\b", " ", normalized)
        normalized = re.sub(
            r"\bAND (ITS )?(AFFILIATES?|AFFILIATED CORPORATIONS?)\b",
            " ",
            normalized,
        )
        normalized = re.sub(
            r"\b(FKA|FORMERLY|AFFILIATES?|AFFILIATED|CORPORATIONS?)\b",
            " ",
            normalized,
        )
        normalized = re.sub(r"\bSUBSIDIAR(?:Y|IES)\b", " ", normalized)
        normalized = re.sub(
            r"\b(INCORPORATED|CORPORATION|COMPANY|COMPANIES|CORP|CO|INC|LLC|LTD|LP|PLC|HOLDINGS|HOLDING|GROUP)\b",
            " ",
            normalized,
        )
        normalized = re.sub(r"\b(U\s*S\s*A|USA|US)\b", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = re.sub(r"\bAND\b$", "", normalized).strip()
        return normalized

    def _build_normalized_lookup(self, mappings: Dict[str, str]) -> Dict[str, str]:
        normalized_lookup: Dict[str, str] = {}
        for company_name, ticker in mappings.items():
            normalized_key = self._normalize_company_name(company_name)
            if normalized_key and normalized_key not in normalized_lookup:
                normalized_lookup[normalized_key] = ticker
        return normalized_lookup

    def _load_sec_normalized_lookup(self) -> Dict[str, str]:
        """
        Load normalized issuer-name -> ticker mappings from SEC's official
        exchange universe table stored in SQLite.

        We only use SEC data as a high-precision fallback (exact normalized
        name match), and we exclude ETF/fund-style records for safer mapping.
        """
        try:
            import config  # type: ignore

            allowed_exchanges_raw = getattr(
                config,
                "SEC_ALLOWED_EXCHANGES",
                ["Nasdaq", "NYSE", "NYSE American"],
            )
        except Exception:
            allowed_exchanges_raw = ["Nasdaq", "NYSE", "NYSE American"]

        def _normalize_exchange(exchange: str) -> str:
            value = re.sub(r"\s+", " ", str(exchange or "").strip().upper())
            if value.startswith("NASDAQ"):
                return "NASDAQ"
            if value.startswith("NYSE AMERICAN"):
                return "NYSE AMERICAN"
            if value.startswith("NYSE"):
                return "NYSE"
            return value

        allowed_exchanges = {
            _normalize_exchange(ex) for ex in (allowed_exchanges_raw or [])
            if str(ex).strip()
        }

        try:
            conn = sqlite3.connect(self.db_path)
            try:
                table_exists = pd.read_sql_query(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'sec_ticker_universe'
                    """,
                    conn,
                )
                if table_exists.empty:
                    return {}

                sec_df = pd.read_sql_query(
                    """
                    SELECT normalized_name, ticker, exchange, COALESCE(is_etf, 0) AS is_etf
                    FROM sec_ticker_universe
                    WHERE ticker IS NOT NULL
                      AND ticker != ''
                      AND normalized_name IS NOT NULL
                      AND normalized_name != ''
                      AND COALESCE(is_etf, 0) = 0
                    """,
                    conn,
                )
            finally:
                conn.close()
        except Exception:
            return {}

        if sec_df.empty:
            return {}

        sec_df["exchange_key"] = sec_df["exchange"].apply(_normalize_exchange)
        if allowed_exchanges:
            sec_df = sec_df[sec_df["exchange_key"].isin(allowed_exchanges)].copy()
        if sec_df.empty:
            return {}

        def _exchange_priority(exchange_key: str) -> int:
            if exchange_key == "NASDAQ":
                return 0
            if exchange_key == "NYSE":
                return 1
            if exchange_key == "NYSE AMERICAN":
                return 2
            return 9

        best_by_name: dict[str, tuple[int, str]] = {}
        for _, row in sec_df.iterrows():
            normalized_name = str(row.get("normalized_name", "")).strip()
            ticker = str(row.get("ticker", "")).strip().upper()
            if not normalized_name or not ticker:
                continue
            priority = _exchange_priority(str(row.get("exchange_key", "")))
            existing = best_by_name.get(normalized_name)
            if existing is None or (priority, ticker) < existing:
                best_by_name[normalized_name] = (priority, ticker)

        return {name: val[1] for name, val in best_by_name.items()}

    @staticmethod
    def is_likely_non_public_entity(company_name: str) -> bool:
        """
        Heuristic filter for entities that are usually not directly investable equities.
        """
        normalized = CompanyMapper._normalize_company_name(company_name)
        if not normalized:
            return True

        blocked_tokens = {
            "ASSOCIATION",
            "COALITION",
            "CHAMBER",
            "COUNCIL",
            "FEDERATION",
            "UNION",
            "COMMITTEE",
            "FOUNDATION",
            "INSTITUTE",
            "SOCIETY",
            "ROUNDTABLE",
            "ACTION FUND",
            "PAC",
            "PARTY",
            "STATE OF",
            "CITY OF",
            "COUNTY OF",
            "UNIVERSITY",
            "SCHOOL",
            "CHURCH",
            "NATION",
        }
        return any(token in normalized for token in blocked_tokens)
    
    def find_ticker_with_info(self, company_name: str) -> tuple:
        """
        Resolve a company name to a ticker and return the match method used.

        Returns
        -------
        (ticker, method) where:
          ticker – str or None
          method – one of "exact" | "suffix" | "case_insensitive" |
                          "normalized" | "sec_normalized" |
                          "prefix" | "fuzzy" | None
        """
        if not company_name:
            return None, None

        # 1) Direct exact match
        if company_name in self.ticker_map:
            return self.ticker_map[company_name], "exact"

        # 2) Strip common legal suffixes then try exact match again
        clean_name = company_name.strip()
        suffixes = [
            ' Inc', ' Inc.', ' Corporation', ' Corp', ' Corp.',
            ' LLC', ' L.L.C.', ' LP', ' L.P.', ' Company', ' Co', ' Co.',
        ]
        for suffix in suffixes:
            if clean_name.endswith(suffix):
                clean_name = clean_name[:-len(suffix)].strip()
                if clean_name in self.ticker_map:
                    return self.ticker_map[clean_name], "suffix"
                break  # only strip once

        # 3) Case-insensitive match
        lower_input = company_name.lower()
        lower_clean = clean_name.lower()
        for key, ticker in self.ticker_map.items():
            if key.lower() == lower_input or key.lower() == lower_clean:
                return ticker, "case_insensitive"

        # 4) Normalized match (strips punctuation, suffixes, "AND SUBSIDIARIES" etc.)
        normalized_name = self._normalize_company_name(clean_name)
        if normalized_name in self.normalized_ticker_map:
            return self.normalized_ticker_map[normalized_name], "normalized"

        # 5) SEC normalized fallback (official exchange-listed universe)
        if normalized_name in self.sec_normalized_ticker_map:
            return self.sec_normalized_ticker_map[normalized_name], "sec_normalized"

        # 6) Prefix fallback — handles "VISA INC AND VARIOUS SUBSIDIARIES"
        tokens = normalized_name.split()
        for token_count in range(len(tokens), 1, -1):
            candidate = " ".join(tokens[:token_count])
            if candidate in self.normalized_ticker_map:
                return self.normalized_ticker_map[candidate], "prefix"

        # 7) Fuzzy matching — last resort; only when enabled in config
        try:
            import config as _cfg
            fuzzy_enabled = getattr(_cfg, 'FUZZY_MATCHING_ENABLED', True)
            cfg_threshold = float(getattr(_cfg, 'FUZZY_MATCHING_THRESHOLD', 85))
            # 0.82 is the safety FLOOR (minimum strictness) to suppress false
            # positives such as "BP AMERICA"→BAC and "DJI TECHNOLOGY"→MU.
            # Higher values are allowed (stricter matching), lower values are
            # forced up to 0.82.
            fuzzy_cutoff = cfg_threshold / 100.0
            if fuzzy_cutoff < 0.82:
                import warnings
                warnings.warn(
                    f"config.FUZZY_MATCHING_THRESHOLD={cfg_threshold} is below "
                    "the 82-point safety floor and will be treated as 82.",
                    stacklevel=3,
                )
                fuzzy_cutoff = 0.82
        except Exception:
            fuzzy_enabled = True
            fuzzy_cutoff = 0.82

        if fuzzy_enabled and normalized_name:
            candidates = list(self.normalized_ticker_map.keys())
            matches = difflib.get_close_matches(
                normalized_name, candidates, n=1, cutoff=fuzzy_cutoff
            )
            if matches:
                matched_key = matches[0]
                ticker = self.normalized_ticker_map[matched_key]
                if ticker is None:
                    return None, None
                # Token-overlap guard: at least one substantive shared token
                # (excluding high-noise generic words) must exist.
                input_tokens = {
                    t for t in normalized_name.split()
                    if len(t) > 2 and t not in self.GENERIC_FUZZY_TOKENS
                }
                match_tokens = {
                    t for t in matched_key.split()
                    if len(t) > 2 and t not in self.GENERIC_FUZZY_TOKENS
                }
                shared_tokens = input_tokens & match_tokens
                if shared_tokens:
                    return ticker, "fuzzy"

        return None, None

    def find_ticker(self, company_name: str) -> Optional[str]:
        """Resolve a company name to a ticker (backward-compatible wrapper)."""
        ticker, _ = self.find_ticker_with_info(company_name)
        return ticker
    
    def add_mapping(self, company_name: str, ticker: str):
        """Add a new company-ticker mapping"""
        self.ticker_map[company_name] = ticker
        normalized_key = self._normalize_company_name(company_name)
        if normalized_key:
            self.normalized_ticker_map[normalized_key] = ticker
