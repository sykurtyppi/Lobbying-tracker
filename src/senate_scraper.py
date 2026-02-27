"""
Senate Lobbying Disclosure Database Scraper
Fetches data from the official Senate lobbying database (lda.senate.gov)
"""

import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time
import json
import math
import sys
import os
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET

# Allow importing config from project root whether this module is run directly
# or imported from src/.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    import config as _cfg
    _API_BASE_DEFAULT = getattr(_cfg, "SENATE_API_BASE", "https://lda.senate.gov/api/v1")
except ImportError:
    _cfg = None
    _API_BASE_DEFAULT = "https://lda.senate.gov/api/v1"


class SenateLobbyingScraper:
    """
    Scraper for Senate Lobbying Disclosure Database
    URL: https://lda.senate.gov/

    The API base URL is read from ``config.SENATE_API_BASE`` so that the
    endpoint can be updated in one place when the LDA migrates from
    lda.senate.gov to lda.gov (sunset scheduled June 30 2026).
    """

    BASE_URL = "https://lda.senate.gov"
    # Class-level default; overridden per-instance from config.
    API_BASE = _API_BASE_DEFAULT

    def __init__(self, api_base: Optional[str] = None):
        # Per-instance override > class default (which already reads config).
        configured_base = (api_base or self.API_BASE).rstrip("/")
        base_candidates = [configured_base, "https://lda.gov/api/v1", "https://lda.senate.gov/api/v1"]
        deduped = []
        for base in base_candidates:
            if base and base not in deduped:
                deduped.append(base)
        self.api_base_candidates = deduped
        self._active_api_index = 0
        self.API_BASE = self.api_base_candidates[self._active_api_index]

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.last_fetch_meta = {}

    def _switch_api_base(self) -> bool:
        """
        Move to the next API base candidate, if available.
        Returns True when switched, False when no fallback remains.
        """
        if self._active_api_index + 1 >= len(self.api_base_candidates):
            return False
        self._active_api_index += 1
        self.API_BASE = self.api_base_candidates[self._active_api_index]
        print(f"  ↪ Switching API base to {self.API_BASE}")
        return True
    
    def get_filings(self, filing_year: int = None, max_pages: int = 1200, max_retries: int = 5) -> List[Dict]:
        """
        Fetch ALL filings for a given year with proper pagination and safety caps.
        
        Args:
            filing_year: Year of filings (default: current year)
            max_pages: Maximum pages to fetch (safety limit)
            max_retries: Maximum number of retries for rate limiting
            
        Returns:
            List of filing dictionaries
        """
        if filing_year is None:
            filing_year = datetime.now().year
        
        print(f"Fetching filings for {filing_year}...")

        self.last_fetch_meta = {
            "year": filing_year,
            "requested_max_pages": max_pages,
            "effective_max_pages": max_pages,
            "api_reported_count": None,
            "fetched_count": 0,
            "pages_fetched": 0,
            "reached_end": False,
            "hit_page_cap": False,
            "incomplete": False,
            "error": None,
        }
        
        # Always use base URL and keep our params
        base_url = f"{self.API_BASE}/filings/"
        
        params = {
            'filing_year': filing_year,
            'ordering': '-dt_posted',
            'page': 1,
        }
        
        all_results = []
        total_count = None
        
        while True:
            page = params['page']
            
            # Safety cap to prevent runaway
            if page > max_pages:
                print(f"🛑 Reached max_pages={max_pages} for {filing_year}, stopping.")
                print(f"   If you need more data, increase max_pages in the function call.")
                self.last_fetch_meta["hit_page_cap"] = True
                self.last_fetch_meta["incomplete"] = True
                break
            
            retry_count = 0
            success = False
            reached_end = False
            
            while retry_count < max_retries and not success:
                try:
                    print(f"  Fetching page {page}...")
                    response = self.session.get(base_url, params=params, timeout=30)
                    
                    if response.status_code == 429:  # Rate limited
                        wait_time = (2 ** retry_count) * 5  # Exponential backoff
                        print(f"  ⚠️  Rate limited. Waiting {wait_time} seconds before retry {retry_count + 1}/{max_retries}...")
                        time.sleep(wait_time)
                        retry_count += 1
                        continue
                    
                    response.raise_for_status()
                    success = True
                    
                    data = response.json()
                    
                    results = data.get('results', [])
                    if not results:
                        print("  ℹ️  No results on this page, stopping.")
                        reached_end = True
                        break

                    # Log total count once and adjust page cap if needed.
                    if total_count is None:
                        total_count = data.get('count')
                        self.last_fetch_meta["api_reported_count"] = total_count
                        if total_count is not None:
                            page_size = len(results) if len(results) > 0 else 25
                            expected_pages = max(1, math.ceil(total_count / page_size))
                            print(f"  📊 API reports {total_count} total filings for {filing_year}")
                            print(f"  📄 Expecting ~{expected_pages} pages")
                            if expected_pages > max_pages:
                                new_cap = expected_pages + 10
                                print(
                                    f"  ⚠️  max_pages={max_pages} is too low for this year."
                                    f" Auto-expanding to {new_cap} pages."
                                )
                                max_pages = new_cap
                                self.last_fetch_meta["effective_max_pages"] = max_pages
                    
                    all_results.extend(results)
                    self.last_fetch_meta["pages_fetched"] = max(
                        int(self.last_fetch_meta.get("pages_fetched", 0)),
                        page,
                    )
                    print(f"  Got {len(results)} filings (total so far: {len(all_results)})")
                    
                    # Check if there are more pages
                    if not data.get('next'):
                        print("  ✅ No more pages (reached end).")
                        reached_end = True
                        self.last_fetch_meta["reached_end"] = True
                        break
                    
                    # Increment page - CRITICAL: keep filing_year in params!
                    params['page'] += 1
                    
                    # Be nice to the API - longer delay between pages
                    time.sleep(2)
                    
                except requests.exceptions.RequestException as e:
                    # Connection/DNS failures can happen during endpoint migration
                    # windows. Fall back to alternate configured API bases before
                    # consuming retries on the same host.
                    if isinstance(e, requests.exceptions.ConnectionError):
                        if self._switch_api_base():
                            base_url = f"{self.API_BASE}/filings/"
                            retry_count = 0
                            continue

                    if retry_count < max_retries - 1:
                        wait_time = (2 ** retry_count) * 5
                        print(f"  ⚠️  Error: {e}")
                        print(f"  Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                        retry_count += 1
                    else:
                        print(f"  ❌ Error fetching filings after {max_retries} retries: {e}")
                        print(f"✅ Retrieved {len(all_results)} total filings for {filing_year} (incomplete)")
                        self.last_fetch_meta["incomplete"] = True
                        self.last_fetch_meta["error"] = str(e)
                        self.last_fetch_meta["fetched_count"] = len(all_results)
                        return self._parse_filings(all_results)
            
            if not success:
                self.last_fetch_meta["incomplete"] = True
                if self.last_fetch_meta.get("error") is None:
                    self.last_fetch_meta["error"] = (
                        f"Failed to fetch page {page} after {max_retries} retries"
                    )
                break

            if reached_end:
                break
        
        print(f"✅ Retrieved {len(all_results)} total filings for {filing_year}")
        self.last_fetch_meta["fetched_count"] = len(all_results)
        self.last_fetch_meta["effective_max_pages"] = max_pages
        if (
            self.last_fetch_meta.get("api_reported_count") is not None
            and len(all_results) < int(self.last_fetch_meta["api_reported_count"])
            and not self.last_fetch_meta.get("reached_end")
        ):
            self.last_fetch_meta["incomplete"] = True
        return self._parse_filings(all_results)
    
    def _parse_filings(self, data: List[Dict]) -> List[Dict]:
        """Parse raw filing data into standardized format"""
        parsed_filings = []
        
        for filing in data:
            try:
                parsed = {
                    'filing_uuid': filing.get('filing_uuid'),
                    'filing_type': (
                        filing.get('filing_type')
                        or filing.get('filing_type_display')
                        or filing.get('document_type')
                    ),
                    'filing_year': filing.get('filing_year'),
                    'filing_period': filing.get('filing_period'),
                    'filing_date': filing.get('dt_posted'),
                    'registrant_name': filing.get('registrant', {}).get('name'),
                    'registrant_id': filing.get('registrant', {}).get('registrant_id'),
                    'client_name': filing.get('client', {}).get('name'),
                    'client_id': filing.get('client', {}).get('client_id'),
                    'amount': self._parse_amount(filing.get('income') or filing.get('expenses')),
                    'lobbying_activities': filing.get('lobbying_activities', []),
                    'government_entities': filing.get('government_entities', [])
                }
                
                parsed_filings.append(parsed)
                
            except Exception as e:
                print(f"Error parsing filing: {e}")
                continue
        
        return parsed_filings
    
    def _parse_amount(self, amount_data) -> float:
        """Parse lobbying amount from various formats"""
        if amount_data is None:
            return 0.0
        
        if isinstance(amount_data, (int, float)):
            return float(amount_data)
        
        if isinstance(amount_data, str):
            # Remove common formatting
            amount_str = amount_data.replace('$', '').replace(',', '').strip()
            
            # Handle ranges (take midpoint)
            if '-' in amount_str or 'to' in amount_str.lower():
                parts = amount_str.replace('to', '-').split('-')
                try:
                    return (float(parts[0]) + float(parts[1])) / 2
                except:
                    return 0.0
            
            try:
                return float(amount_str)
            except:
                return 0.0
        
        return 0.0
    
    def get_registrant_filings(self, registrant_id: str, year: int = None) -> List[Dict]:
        """Get all filings for a specific registrant"""
        url = f"{self.API_BASE}/registrants/{registrant_id}/filings/"
        
        params = {}
        if year:
            params['filing_year'] = year
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching registrant filings: {e}")
            return []
    
    def get_client_filings(self, client_name: str, year: int = None) -> List[Dict]:
        """Get all filings for a specific client (company)"""
        # Search for client
        url = f"{self.API_BASE}/clients/"
        
        params = {
            'client_name': client_name
        }
        
        if year:
            params['filing_year'] = year
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching client filings: {e}")
            return []
    
    def aggregate_by_client(self, filings: List[Dict]) -> pd.DataFrame:
        """Aggregate filings by client company"""
        if not filings:
            return pd.DataFrame()
        
        df = pd.DataFrame(filings)
        
        # Aggregate by client
        aggregated = df.groupby(['client_name', 'filing_year', 'filing_period']).agg({
            'amount': 'sum',
            'filing_uuid': 'count'
        }).reset_index()
        
        aggregated.columns = ['client_name', 'year', 'period', 'total_amount', 'num_filings']
        
        return aggregated
    
    def fetch_bulk_data(self, start_year: int = 2020, end_year: int = None) -> pd.DataFrame:
        """
        Fetch bulk lobbying data across multiple years
        This is the main function to build your database
        """
        if end_year is None:
            end_year = datetime.now().year
        
        all_filings = []
        
        for year in range(start_year, end_year + 1):
            print(f"\nFetching data for {year}...")
            
            filings = self.get_filings(filing_year=year)
            all_filings.extend(filings)
            
            # Rate limiting - be nice to the API
            time.sleep(1)
            
            print(f"  Retrieved {len(filings)} filings for {year}")
        
        print(f"\n{'='*70}")
        print(f"Total filings retrieved: {len(all_filings)}")
        print(f"{'='*70}\n")
        
        # Convert to DataFrame
        df = pd.DataFrame(all_filings)
        
        return df
    
    def get_top_spenders(self, year: int, limit: int = 100) -> pd.DataFrame:
        """Get top lobbying spenders for a given year"""
        filings = self.get_filings(filing_year=year)
        
        if not filings:
            return pd.DataFrame()
        
        # Aggregate by client
        aggregated = self.aggregate_by_client(filings)
        
        # Sum across all quarters for the year
        yearly_totals = aggregated.groupby('client_name').agg({
            'total_amount': 'sum',
            'num_filings': 'sum'
        }).reset_index()
        
        # Sort and limit
        top_spenders = yearly_totals.nlargest(limit, 'total_amount')
        
        return top_spenders


class LobbyingDataEnricher:
    """
    Enriches lobbying data with market information
    """
    
    def __init__(self):
        self.ticker_cache = {}
    
    def match_company_to_ticker(self, company_name: str) -> Optional[str]:
        """
        Attempt to match company name to stock ticker
        Uses multiple strategies including direct lookup and fuzzy matching
        """
        # Check cache first
        if company_name in self.ticker_cache:
            return self.ticker_cache[company_name]
        
        # Common name variations
        name_variations = [
            company_name,
            company_name.replace(' Inc', '').replace(' Corporation', '').replace(' Corp', ''),
            company_name.split()[0]  # First word
        ]
        
        # Try to find ticker using yfinance search
        # This is a placeholder - you'd implement more sophisticated matching
        
        return None
    
    def enrich_with_market_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add market cap and sector information to lobbying data"""
        # This would fetch market data for each company
        # Implementation depends on your data source
        
        return df


# Example usage function
def build_initial_database():
    """
    Example function to build your initial database
    Run this once to populate your database with historical data
    """
    scraper = SenateLobbyingScraper()
    
    # Fetch data from 2020 onwards
    print("Building initial database...")
    print("This may take 10-15 minutes depending on the data volume...\n")
    
    df = scraper.fetch_bulk_data(start_year=2020)
    
    # Save to CSV
    output_file = 'data/senate_lobbying_raw.csv'
    df.to_csv(output_file, index=False)
    print(f"\nData saved to {output_file}")
    
    # Show summary
    print("\nData Summary:")
    print(f"Total records: {len(df)}")
    print(f"Unique companies: {df['client_name'].nunique()}")
    print(f"Total lobbying amount: ${df['amount'].sum():,.2f}")
    print(f"Date range: {df['filing_date'].min()} to {df['filing_date'].max()}")
    
    return df


if __name__ == "__main__":
    # Test the scraper
    scraper = SenateLobbyingScraper()
    
    # Fetch current year data
    current_year = datetime.now().year
    filings = scraper.get_filings(filing_year=current_year)
    
    print(f"Retrieved {len(filings)} filings for {current_year}")
    
    if filings:
        # Show sample
        df = pd.DataFrame(filings[:5])
        print("\nSample filings:")
        print(df[['client_name', 'amount', 'filing_year', 'filing_period']])
