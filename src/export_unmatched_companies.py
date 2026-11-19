#!/usr/bin/env python3
"""
Export unmatched lobbying clients to CSV for manual / assisted ticker mapping.

This will read from:
  - lobbying_filings (raw filings)
  - company_lobbying (aggregated)

and produce:
  - data/unmatched_companies.csv

You then fill in the TICKER column and run the second script to generate
a config snippet for CUSTOM_TICKER_MAPPINGS.
"""

import csv
import sqlite3
import pandas as pd
from pathlib import Path
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
try:
    from data_fetcher import CompanyMapper
except Exception:
    CompanyMapper = None

DB_PATH = "data/lobbying_data.db"
OUTPUT_CSV = "data/unmatched_companies.csv"

def _refuse_filled_export(output_csv: str):
    """Fail closed when an existing CSV is not clearly free of manual tickers."""
    output_path = Path(output_csv)
    if output_path.exists():
        with output_path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or reader.fieldnames.count("TICKER") != 1:
                raise ValueError(
                    f"Refusing to overwrite {output_csv}: unrecognized CSV format "
                    "or missing TICKER column. Use a new output path to re-export."
                )
            filled = False
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(
                        f"Refusing to overwrite {output_csv}: malformed CSV row. "
                        "Use a new output path to re-export."
                    )
                if row["TICKER"].strip():
                    filled = True
                    break
        if filled:
            raise ValueError(
                "Refusing to overwrite existing manual ticker mapping in "
                f"{output_csv}: a non-empty TICKER is already filled. Delete "
                "the file or pass a new output path to re-export."
            )

def export_unmatched(db_path: str = DB_PATH, output_csv: str = OUTPUT_CSV):
    # Refuse before opening the database or rebuilding suggestions.
    _refuse_filled_export(output_csv)
    conn = sqlite3.connect(db_path)

    # Aggregate raw filings to one row per alias across all available years.
    raw_clients = pd.read_sql_query(
        """
        SELECT
            TRIM(client_name) AS company_name,
            MIN(year) AS first_year,
            MAX(year) AS latest_year,
            COUNT(DISTINCT year) AS years_present,
            SUM(amount) AS total_spend,
            COUNT(*) AS num_filings
        FROM lobbying_filings
        WHERE client_name IS NOT NULL AND TRIM(client_name) != ''
        GROUP BY TRIM(client_name)
        """,
        conn,
    )

    # Current mapped aliases from either company_lobbying or entity_aliases.
    mapped_company_names = pd.read_sql_query(
        """
        SELECT DISTINCT TRIM(company_name) AS company_name
        FROM company_lobbying
        WHERE ticker IS NOT NULL AND TRIM(ticker) != ''
        """,
        conn,
    )
    alias_table_exists = pd.read_sql_query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_aliases'",
        conn,
    )
    if alias_table_exists.empty:
        mapped_alias_names = pd.DataFrame(columns=["company_name"])
    else:
        mapped_alias_names = pd.read_sql_query(
            """
            SELECT DISTINCT TRIM(alias_name) AS company_name
            FROM entity_aliases
            WHERE ticker IS NOT NULL
              AND TRIM(ticker) != ''
              AND LOWER(COALESCE(status, 'verified')) IN ('verified', 'approved', 'active')
            """,
            conn,
        )

    mapped_set = set(mapped_company_names["company_name"].dropna().tolist())
    mapped_set |= set(mapped_alias_names["company_name"].dropna().tolist())

    unmatched = raw_clients[~raw_clients["company_name"].isin(mapped_set)].copy()
    unmatched = unmatched.sort_values(by=["total_spend"], ascending=False)

    if CompanyMapper is not None and not unmatched.empty:
        mapper = CompanyMapper(db_path=db_path)
        unmatched["normalized_name"] = unmatched["company_name"].apply(
            CompanyMapper._normalize_company_name
        )
        unmatched["likely_non_public"] = unmatched["company_name"].apply(
            CompanyMapper.is_likely_non_public_entity
        )
        suggestions = unmatched["company_name"].apply(mapper.find_ticker_with_info)
        unmatched["suggested_ticker"] = suggestions.apply(lambda x: x[0] if x else None)
        unmatched["suggested_method"] = suggestions.apply(lambda x: x[1] if x else None)
    else:
        unmatched["normalized_name"] = ""
        unmatched["likely_non_public"] = False
        unmatched["suggested_ticker"] = None
        unmatched["suggested_method"] = None

    # Ensure directory exists
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)

    # Add empty TICKER column for you to fill
    unmatched["TICKER"] = ""

    # The database is no longer needed; close it even if the late guard refuses.
    conn.close()
    # A user may have filled the CSV while the database and mapper were read.
    _refuse_filled_export(output_csv)
    unmatched.to_csv(output_csv, index=False)

    non_public_n = int(unmatched["likely_non_public"].sum()) if not unmatched.empty else 0
    suggested_n = int(unmatched["suggested_ticker"].notna().sum()) if not unmatched.empty else 0
    print(f"✅ Exported {len(unmatched)} unmatched aliases to {output_csv}")
    print(f"   Likely non-public entities flagged: {non_public_n:,}")
    print(f"   Auto suggestions available       : {suggested_n:,}")
    print("   Fill in TICKER for investable entities, then import via Settings.")


if __name__ == "__main__":
    export_unmatched()
