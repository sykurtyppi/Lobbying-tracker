#!/usr/bin/env python3
"""
Database Diagnostic Tool
Analyzes what data was actually fetched and stored
"""

import sqlite3
import pandas as pd


def _fmt_year(value) -> str:
    """Render year values as clean integers when possible."""
    try:
        if pd.isna(value):
            return "N/A"
        return str(int(float(value)))
    except Exception:
        return str(value)


def analyze_database(db_path="data/lobbying_data.db"):
    """Comprehensive analysis of what's in the database"""

    conn = sqlite3.connect(db_path)
    try:
        print("="*70)
        print("DATABASE DIAGNOSTIC REPORT")
        print("="*70)

        # 1. Check lobbying_filings table (raw filings)
        print("\n📄 RAW FILINGS TABLE (lobbying_filings)")
        print("-"*70)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM lobbying_filings")
        total_filings = cursor.fetchone()[0]
        print(f"Total raw filings: {total_filings:,}")

        # Filings by year
        print("\nFilings by Year:")
        year_breakdown = pd.read_sql_query("""
            SELECT
                year,
                COUNT(*) as num_filings,
                COUNT(DISTINCT client_name) as unique_clients,
                SUM(amount) as total_amount
            FROM lobbying_filings
            GROUP BY year
            ORDER BY year DESC
        """, conn)

        for _, row in year_breakdown.iterrows():
            yr = _fmt_year(row["year"])
            print(f"  {yr}: {int(row['num_filings']):>6,} filings, "
                  f"{int(row['unique_clients']):>4,} companies, "
                  f"${row['total_amount']/1e6:>8,.1f}M total")

        # Filings by year and quarter
        print("\nFilings by Year & Quarter:")
        quarter_breakdown = pd.read_sql_query("""
            SELECT
                year,
                period,
                COUNT(*) as num_filings
            FROM lobbying_filings
            GROUP BY year, period
            ORDER BY year DESC, period
        """, conn)

        for _, row in quarter_breakdown.iterrows():
            yr = _fmt_year(row["year"])
            period = str(row["period"])
            print(f"  {yr} {period:4s}: {int(row['num_filings']):>6,} filings")

        # 2. Check company_lobbying table (aggregated data)
        print("\n\n🏢 COMPANY LOBBYING TABLE (company_lobbying)")
        print("-"*70)

        cursor.execute("SELECT COUNT(*) FROM company_lobbying")
        total_companies = cursor.fetchone()[0]
        print(f"Total company-quarter records: {total_companies:,}")

        company_breakdown = pd.read_sql_query("""
            SELECT
                year,
                COUNT(*) as num_records,
                COUNT(DISTINCT company_name) as unique_companies,
                COUNT(
                    DISTINCT CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE TRIM(company_name)
                    END
                ) AS unique_entities,
                COUNT(DISTINCT ticker) as unique_tickers,
                SUM(CASE WHEN ticker IS NOT NULL AND ticker != '' THEN 1 ELSE 0 END) as matched_to_ticker,
                COUNT(
                    DISTINCT CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE NULL
                    END
                ) AS matched_entities,
                SUM(CASE WHEN ticker IS NOT NULL AND ticker != '' THEN total_lobbying_spend ELSE 0 END) as matched_spend,
                SUM(total_lobbying_spend) as total_spend
            FROM company_lobbying
            GROUP BY year
            ORDER BY year DESC
        """, conn)

        print("\nCompany Data by Year:")
        for _, row in company_breakdown.iterrows():
            yr = _fmt_year(row["year"])
            match_rate = (row['matched_to_ticker'] / row['num_records'] * 100) if row['num_records'] > 0 else 0
            entity_match_rate = (
                row["matched_entities"] / row["unique_entities"] * 100
                if row["unique_entities"] > 0 else 0
            )
            spend_rate = (row['matched_spend'] / row['total_spend'] * 100) if row['total_spend'] > 0 else 0
            print(f"  {yr}: {int(row['num_records']):>4,} records, "
                  f"{int(row['unique_companies']):>4,} companies, "
                  f"{int(row['unique_entities']):>4,} entities, "
                  f"{int(row['unique_tickers']):>3,} tickers "
                  f"({entity_match_rate:.1f}% entities / {match_rate:.1f}% rows matched), "
                  f"{spend_rate:.1f}% spend coverage, "
                  f"${row['total_spend']/1e6:>8,.1f}M")

        # Match-method audit by latest year (mapping provenance)
        print("\nMatch Method Breakdown (latest year):")
        mm = pd.read_sql_query(
            """
            WITH latest_year AS (
                SELECT MAX(year) AS y FROM company_lobbying
            )
            SELECT
                COALESCE(NULLIF(match_method, ''), 'unknown') AS match_method,
                COUNT(*) AS n_rows,
                SUM(total_lobbying_spend) AS spend
            FROM company_lobbying
            WHERE year = (SELECT y FROM latest_year)
            GROUP BY COALESCE(NULLIF(match_method, ''), 'unknown')
            ORDER BY n_rows DESC
            """,
            conn,
        )
        if mm.empty:
            print("  No match_method data found.")
        else:
            total_mm_rows = mm["n_rows"].sum()
            for _, row in mm.iterrows():
                pct = (row["n_rows"] / total_mm_rows * 100) if total_mm_rows else 0
                spend_m = (row["spend"] or 0) / 1e6
                print(
                    f"  {row['match_method']:<16} {int(row['n_rows']):>7,} rows "
                    f"({pct:>5.1f}%)  spend ${spend_m:>9,.1f}M"
                )

        # Ingestion-run audit coverage
        print("\nIngestion Audit Coverage:")
        runs_table = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ingestion_runs'",
            conn
        )
        if runs_table.empty:
            print("  ⚠️  ingestion_runs table missing")
        else:
            latest_runs = pd.read_sql_query(
                """
                WITH ranked AS (
                    SELECT
                        r.year,
                        r.status,
                        r.is_complete,
                        r.started_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY r.year
                            ORDER BY r.started_at DESC, r.id DESC
                        ) AS rn
                    FROM ingestion_runs r
                )
                SELECT year, status, is_complete, started_at
                FROM ranked
                WHERE rn = 1
                ORDER BY year DESC
                """,
                conn,
            )
            data_years = sorted(company_breakdown["year"].tolist())
            run_years = sorted(latest_runs["year"].tolist()) if not latest_runs.empty else []
            missing_run_years = sorted(set(data_years) - set(run_years))

            if latest_runs.empty:
                print("  ⚠️  No ingestion run records found")
            else:
                for _, row in latest_runs.iterrows():
                    comp_flag = "complete" if int(row["is_complete"] or 0) == 1 else "incomplete"
                    print(f"  {int(row['year'])}: {row['status']} ({comp_flag})")
            if missing_run_years:
                print(f"  ⚠️  Years with data but no ingestion audit row: {missing_run_years}")
            else:
                print("  ✅ Every data year has ingestion audit metadata")

        # Data integrity checks
        print("\nData Integrity Checks:")
        invalid_quarters = pd.read_sql_query(
            """
            SELECT COUNT(*) AS n
            FROM company_lobbying
            WHERE quarter NOT IN ('Q1', 'Q2', 'Q3', 'Q4')
               OR quarter IS NULL
               OR TRIM(quarter) = ''
            """,
            conn,
        ).iloc[0]["n"]
        non_positive_spend = pd.read_sql_query(
            """
            SELECT COUNT(*) AS n
            FROM company_lobbying
            WHERE total_lobbying_spend IS NULL OR total_lobbying_spend <= 0
            """,
            conn,
        ).iloc[0]["n"]
        print(f"  Invalid quarter labels : {int(invalid_quarters):,}")
        print(f"  Non-positive spend rows: {int(non_positive_spend):,}")
        if int(non_positive_spend) > 0:
            print(
                "    ↳ Run clean_nonpositive_company_spend(dry_run=False) to remove legacy zero rows."
            )

        alias_collisions = pd.read_sql_query(
            """
            SELECT
                year,
                quarter,
                ticker,
                COUNT(DISTINCT company_name) AS alias_count,
                SUM(total_lobbying_spend) AS total_spend
            FROM company_lobbying
            WHERE ticker IS NOT NULL
              AND TRIM(ticker) != ''
            GROUP BY year, quarter, ticker
            HAVING COUNT(DISTINCT company_name) > 1
            ORDER BY alias_count DESC, total_spend DESC
            LIMIT 10
            """,
            conn,
        )
        if alias_collisions.empty:
            print("  Alias collisions       : none")
        else:
            print(
                "  Alias collisions       : "
                f"{len(alias_collisions):,} high-impact ticker-quarter cases (top 10 shown)"
            )
            for _, row in alias_collisions.iterrows():
                yr = _fmt_year(row["year"])
                print(
                    f"    - {yr} {row['quarter']} {row['ticker']}: "
                    f"{int(row['alias_count'])} aliases, ${row['total_spend']/1e6:,.1f}M"
                )

        # 3. Top companies with tickers matched
        print("\n\n⭐ TOP 20 COMPANIES (With Tickers Matched)")
        print("-"*70)

        top_matched = pd.read_sql_query("""
            SELECT
                company_name,
                ticker,
                year,
                SUM(total_lobbying_spend) as total_spend,
                market_cap
            FROM company_lobbying
            WHERE ticker IS NOT NULL
              AND ticker != ''
            GROUP BY company_name, ticker, year
            ORDER BY total_spend DESC
            LIMIT 20
        """, conn)

        if len(top_matched) > 0:
            for _, row in top_matched.iterrows():
                mcap_str = f"${row['market_cap']/1e9:.1f}B" if pd.notna(row['market_cap']) else "N/A"
                print(f"  {row['ticker']:6s} {row['company_name'][:30]:30s} "
                      f"{row['year']} ${row['total_spend']/1e6:>6.2f}M  MCap: {mcap_str}")
        else:
            print("  No companies matched to tickers yet")

        # 4. Top companies WITHOUT tickers (need mapping)
        print("\n\n🔍 TOP 20 COMPANIES NEEDING TICKER MAPPING")
        print("-"*70)

        top_unmatched = pd.read_sql_query("""
            SELECT
                client_name as company_name,
                SUM(amount) as total_spend,
                COUNT(*) as num_filings,
                MIN(year) as first_year,
                MAX(year) as latest_year
            FROM lobbying_filings
            WHERE client_name NOT IN (
                SELECT DISTINCT company_name
                FROM company_lobbying
                WHERE ticker IS NOT NULL
                  AND ticker != ''
            )
            GROUP BY client_name
            ORDER BY total_spend DESC
            LIMIT 500
        """, conn)

        if len(top_unmatched) > 0:
            try:
                from src.data_fetcher import CompanyMapper
                top_unmatched["likely_non_public"] = top_unmatched["company_name"].apply(
                    CompanyMapper.is_likely_non_public_entity
                )
            except Exception:
                top_unmatched["likely_non_public"] = False

            investable_unmatched = top_unmatched[~top_unmatched["likely_non_public"]].head(20)
            non_public_unmatched = top_unmatched[top_unmatched["likely_non_public"]].head(10)

            if len(investable_unmatched) > 0:
                print("\n💡 Highest-impact INVESTABLE names to add in CUSTOM_TICKER_MAPPINGS:")
                for _, row in investable_unmatched.iterrows():
                    print(
                        f"  '{row['company_name']}': 'TICK',  "
                        f"# ${row['total_spend']/1e6:.1f}M across {int(row['first_year'])}-{int(row['latest_year'])}"
                    )
            else:
                print("\n✅ No obvious high-spend investable gaps found in top unmatched names.")

            if len(non_public_unmatched) > 0:
                print(
                    f"\nℹ️  {len(top_unmatched[top_unmatched['likely_non_public']]):,} high-spend unmatched names "
                    "look non-investable (associations/governments/non-profits)."
                )

        # 5. Data quality issues
        print("\n\n⚠️  DATA QUALITY CHECKS")
        print("-"*70)

        # Check for duplicates
        cursor.execute("""
            SELECT COUNT(*) FROM (
                SELECT company_name, year, quarter, COUNT(*) as cnt
                FROM company_lobbying
                GROUP BY company_name, year, quarter
                HAVING cnt > 1
            )
        """)
        duplicates = cursor.fetchone()[0]

        if duplicates > 0:
            print(f"  ⚠️  Found {duplicates} duplicate company-year-quarter records")
        else:
            print(f"  ✅ No duplicate records")

        # Check for zero amounts
        cursor.execute("SELECT COUNT(*) FROM lobbying_filings WHERE amount = 0 OR amount IS NULL")
        zero_amounts = cursor.fetchone()[0]

        if zero_amounts > 0:
            print(f"  ⚠️  {zero_amounts:,} filings have $0 or NULL amounts ({zero_amounts/total_filings*100:.1f}%)")
        else:
            print(f"  ✅ All filings have valid amounts")

        # Check date range
        cursor.execute("SELECT MIN(filing_date), MAX(filing_date) FROM lobbying_filings WHERE filing_date IS NOT NULL")
        min_date, max_date = cursor.fetchone()
        if min_date and max_date:
            print(f"  📅 Filing date range: {min_date} to {max_date}")

        # 6. Summary recommendations
        print("\n\n💡 RECOMMENDATIONS")
        print("-"*70)

        # Calculate match rates (rows + spend)
        cursor.execute("""
            SELECT
                COUNT(*) as total_rows,
                SUM(CASE WHEN ticker IS NOT NULL AND ticker != '' THEN 1 ELSE 0 END) as matched_rows,
                SUM(total_lobbying_spend) as total_spend,
                SUM(CASE WHEN ticker IS NOT NULL AND ticker != '' THEN total_lobbying_spend ELSE 0 END) as matched_spend
            FROM company_lobbying
        """)
        total_rows, matched_rows, total_spend, matched_spend = cursor.fetchone()
        if total_rows > 0:
            row_rate = (matched_rows / total_rows * 100)
            spend_rate = (matched_spend / total_spend * 100) if total_spend else 0
            print(f"  📊 Ticker match rate (rows): {row_rate:.1f}%")
            print(f"  💵 Ticker coverage (spend): {spend_rate:.1f}%")
            if spend_rate < 30:
                print("     → Spend coverage is still low for institutional research; prioritize high-spend investable aliases.")

        print("\n" + "="*70)
        print("END OF REPORT")
        print("="*70)
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    db_path = "data/lobbying_data.db"
    if len(sys.argv) > 1:
        db_path = sys.argv[1]

    try:
        analyze_database(db_path)
    except Exception as e:
        print(f"Error analyzing database: {e}")
        import traceback
        traceback.print_exc()
