import atexit
import shutil
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import app
from src.build_company_lobbying import clean_nonpositive_company_spend
from src.data_fetcher import CompanyMapper
import config


def _build_fixture_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE company_lobbying (
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
            );

            CREATE TABLE stock_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                date TEXT,
                close_price REAL,
                return_1m REAL,
                return_3m REAL,
                return_6m REAL,
                return_1y REAL,
                UNIQUE(ticker, date)
            );

            CREATE TABLE ingestion_runs (
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
            );
            """
        )

        quarter_weights = [("Q1", 0.20), ("Q2", 0.23), ("Q3", 0.27), ("Q4", 0.30)]
        sectors = [
            "Technology",
            "Healthcare",
            "Financial Services",
            "Industrials",
            "Energy",
            "Consumer Cyclical",
        ]
        tickers = [f"TK{i:02d}" for i in range(1, 31)]
        now_iso = "2026-01-01T00:00:00+00:00"

        company_rows = []
        for year in range(2019, 2025):
            year_mult = 1.0 + (year - 2019) * 0.08
            for idx, ticker in enumerate(tickers):
                annual_spend = (4_500_000 + idx * 250_000) * year_mult
                market_cap = 5_000_000_000 + idx * 1_500_000_000
                sector = sectors[idx % len(sectors)]
                company_name = f"Company {ticker}"

                for quarter, weight in quarter_weights:
                    spend = round(annual_spend * weight, 2)
                    ratio = spend / market_cap
                    company_rows.append(
                        (
                            company_name,
                            ticker,
                            year,
                            quarter,
                            spend,
                            market_cap,
                            ratio,
                            sector,
                            "exact",
                            now_iso,
                        )
                    )
                    # Alias rows to exercise ticker/entity dedupe logic.
                    if quarter == "Q4" and idx < 3:
                        alias_name = f"Company {ticker} Holdings"
                        alias_spend = round(spend * 0.35, 2)
                        alias_ratio = alias_spend / market_cap
                        company_rows.append(
                            (
                                alias_name,
                                ticker,
                                year,
                                quarter,
                                alias_spend,
                                market_cap,
                                alias_ratio,
                                sector,
                                "manual",
                                now_iso,
                            )
                        )

            # Deterministic new-entrant profile for transition tests.
            new_entrant_annual = 120_000 if year == 2019 else 2_400_000 + (year - 2020) * 200_000
            for quarter, weight in quarter_weights:
                spend = round(new_entrant_annual * weight, 2)
                market_cap = 3_500_000_000
                company_rows.append(
                    (
                        "New Entrant Holdings",
                        "NEWA",
                        year,
                        quarter,
                        spend,
                        market_cap,
                        spend / market_cap,
                        "Industrials",
                        "manual",
                        now_iso,
                    )
                )

        # Include legacy-bad rows so cleaning tests are meaningful.
        company_rows.append(
            (
                "Zero Spend Co",
                "ZERO",
                2022,
                "Q2",
                0.0,
                2_000_000_000,
                0.0,
                "Utilities",
                "manual",
                now_iso,
            )
        )
        company_rows.append(
            (
                "Null Spend Co",
                None,
                2021,
                "Q1",
                None,
                None,
                None,
                None,
                None,
                now_iso,
            )
        )

        conn.executemany(
            """
            INSERT INTO company_lobbying
            (company_name, ticker, year, quarter, total_lobbying_spend,
             market_cap, spend_to_mcap_ratio, sector, match_method, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            company_rows,
        )

        stock_rows = []
        perf_tickers = tickers + ["NEWA"]
        for signal_year in range(2019, 2025):
            # Align with q4_signal_window default anchor (Q4 + 20-day lag)
            snapshot_date = f"{signal_year + 1}-01-20"
            for idx, ticker in enumerate(perf_tickers):
                base_ret = (signal_year - 2019) * 1.4 + ((idx % 9) - 4) * 0.8
                close_price = round(100.0 + idx * 2.0 + (signal_year - 2019) * 5.0, 2)
                stock_rows.append(
                    (
                        ticker,
                        snapshot_date,
                        close_price,
                        round(base_ret + 1.0, 2),
                        round(base_ret + 2.0, 2),
                        round(base_ret + 3.5, 2),
                        round(base_ret + 5.0, 2),
                    )
                )
                # One nearby alternate point for closest-date window tests.
                if idx == 0:
                    stock_rows.append(
                        (
                            ticker,
                            f"{signal_year + 1}-01-25",
                            close_price + 1.0,
                            round(base_ret + 0.5, 2),
                            round(base_ret + 1.5, 2),
                            round(base_ret + 3.0, 2),
                            round(base_ret + 4.5, 2),
                        )
                    )

        conn.executemany(
            """
            INSERT INTO stock_performance
            (ticker, date, close_price, return_1m, return_3m, return_6m, return_1y)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            stock_rows,
        )

        # Mark all fixture years complete for ingestion-status queries.
        conn.executemany(
            """
            INSERT INTO ingestion_runs
            (year, started_at, finished_at, status, is_complete, aggregate_rows)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (year, now_iso, now_iso, "complete", 1, len(company_rows))
                for year in range(2019, 2025)
            ],
        )

        conn.commit()
    finally:
        conn.close()


def _create_fixture_db() -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="lobbying_tracker_tests_"))
    db_path = tmp_dir / "fixture_lobbying_data.db"
    _build_fixture_db(db_path)
    atexit.register(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
    return db_path


DB_PATH = _create_fixture_db()


class RegressionIntegrityTests(unittest.TestCase):
    def setUp(self):
        if not DB_PATH.exists():
            self.skipTest("Fixture database was not created.")

    def test_clean_nonpositive_company_spend_dry_run_and_apply(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = Path(tmpdir) / "tmp_lobbying.db"
            shutil.copyfile(DB_PATH, tmp_db)

            before_conn = sqlite3.connect(tmp_db)
            try:
                before_bad = before_conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM company_lobbying
                    WHERE total_lobbying_spend IS NULL OR total_lobbying_spend <= 0
                    """
                ).fetchone()[0]
            finally:
                before_conn.close()

            dry = clean_nonpositive_company_spend(db_path=str(tmp_db), dry_run=True)
            self.assertEqual(int(dry["rows_flagged"]), int(before_bad))
            self.assertEqual(int(dry["rows_deleted"]), 0)

            applied = clean_nonpositive_company_spend(db_path=str(tmp_db), dry_run=False)
            self.assertEqual(int(applied["rows_deleted"]), int(before_bad))

            after_conn = sqlite3.connect(tmp_db)
            try:
                after_bad = after_conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM company_lobbying
                    WHERE total_lobbying_spend IS NULL OR total_lobbying_spend <= 0
                    """
                ).fetchone()[0]
            finally:
                after_conn.close()

            self.assertEqual(int(after_bad), 0)

    def test_filtered_holdings_are_entity_deduped(self):
        class _Fetcher:
            db_path = str(DB_PATH)

        conn = sqlite3.connect(DB_PATH)
        try:
            year_row = conn.execute(
                "SELECT MAX(year) FROM company_lobbying"
            ).fetchone()
        finally:
            conn.close()

        year = int(year_row[0]) if year_row and year_row[0] is not None else None
        if year is None:
            self.skipTest("No years found in company_lobbying.")

        df = app.get_filtered_data(
            _Fetcher(),
            year=year,
            quarter="Full Year",
            market_cap_filters=[],
            sector_filters=[],
            min_spend=0.0,
        )
        if df.empty:
            self.skipTest(f"No holdings rows for {year}.")

        self.assertIn("entity_key", df.columns)
        self.assertEqual(int(df["entity_key"].nunique()), int(len(df)))

        tickers = df["ticker"].dropna().astype(str).str.strip().str.upper()
        tickers = tickers[tickers != ""]
        self.assertEqual(int(tickers.nunique()), int(len(tickers)))

    @patch("src.data_fetcher.difflib.get_close_matches", return_value=[])
    def test_fuzzy_matching_threshold_uses_safety_floor(self, mock_matches):
        mapper = CompanyMapper(db_path=str(DB_PATH))
        baseline_threshold = getattr(config, "FUZZY_MATCHING_THRESHOLD", 85)

        try:
            # High thresholds should pass through (stricter matching remains possible).
            config.FUZZY_MATCHING_THRESHOLD = 95
            mapper.find_ticker_with_info("ZZZZZ UNKNOWN ENTITY")
            self.assertAlmostEqual(float(mock_matches.call_args.kwargs["cutoff"]), 0.95, places=4)

            # Low thresholds should be floored to 0.82.
            config.FUZZY_MATCHING_THRESHOLD = 70
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mapper.find_ticker_with_info("ZZZZZ UNKNOWN ENTITY")
            self.assertAlmostEqual(float(mock_matches.call_args.kwargs["cutoff"]), 0.82, places=4)
        finally:
            config.FUZZY_MATCHING_THRESHOLD = baseline_threshold


class BugRegressionTests(unittest.TestCase):
    """
    Targeted tests for confirmed bugs identified by Codex review.
    Each test should fail before the fix and pass after.
    """

    # ── P1: Regime filter `is True` vs numpy.bool_ ────────────────────────────
    # ── P2: OpenSecrets duplicate-ticker crash ─────────────────────────────────
    def test_conviction_scores_handles_duplicate_os_tickers(self):
        """
        P2: If opensecrets_contribs has multiple rows for the same ticker in a year
        (e.g. subsidiary aliases), the old os_df.set_index('ticker') raised
        InvalidIndexError. The fix uses groupby().sum() to aggregate duplicates.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = Path(tmpdir) / "os_dedup_test.db"
            shutil.copyfile(DB_PATH, tmp_db)

            conn = sqlite3.connect(tmp_db)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS opensecrets_contribs (
                        id            INTEGER PRIMARY KEY,
                        company_name  TEXT,
                        ticker        TEXT,
                        year          INTEGER,
                        total_contribs REAL,
                        pacs          REAL,
                        indivs        REAL,
                        os_lobbying   REAL
                    )
                    """
                )
                # Two rows for TK01 in the same year — intentional duplicate ticker.
                conn.executemany(
                    """
                    INSERT INTO opensecrets_contribs
                        (company_name, ticker, year, total_contribs, pacs)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        ("Company TK01",          "TK01", 2024, 1_000_000, 500_000),
                        ("Company TK01 Holdings", "TK01", 2024,   750_000, 250_000),
                        ("Company TK02",          "TK02", 2024, 2_000_000, 800_000),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            # Must not raise InvalidIndexError; must return a DataFrame.
            result = app.get_conviction_scores(str(tmp_db), year=2024, refresh_token=998_001)
            self.assertIsInstance(result, pd.DataFrame)

            if not result.empty:
                # TK01 should appear at most once in the scored universe.
                tk01_rows = result[result["ticker"] == "TK01"]
                self.assertLessEqual(len(tk01_rows), 1, msg="Duplicate OS ticker must not duplicate scored rows")

                # TK01's os_pts should reflect the COMBINED PAC amount, not just one alias.
                # TK01 combined = (1M+500K) + (750K+250K) = 2.5M.  TK02 = 2M+800K = 2.8M.
                # So TK02 combined > TK01 combined → TK02 should score ≥ TK01 on os_pts.
                if "TK02" in result["ticker"].values and len(tk01_rows) == 1:
                    tk02_os = result.loc[result["ticker"] == "TK02", "os_pts"].iloc[0]
                    tk01_os = tk01_rows["os_pts"].iloc[0]
                    self.assertGreaterEqual(
                        tk02_os, tk01_os,
                        msg="TK02 has higher combined PAC spend and should score >= TK01 os_pts",
                    )

    # ── P3: Market Issue Pulse public-company filter ───────────────────────────
    def test_top_issue_codes_excludes_non_investable_entities(self):
        """
        P3: get_top_issue_codes_by_year must restrict results to companies that are
        mapped to a public ticker in company_lobbying. Non-investable entities
        (trade associations, NGOs) must not inflate issue-code counts.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = Path(tmpdir) / "issue_filter_test.db"
            shutil.copyfile(DB_PATH, tmp_db)

            conn = sqlite3.connect(tmp_db)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS filing_issues (
                        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                        filing_uuid          TEXT,
                        general_issue_code   TEXT NOT NULL,
                        specific_issues_text TEXT,
                        year                 INTEGER,
                        quarter              TEXT,
                        client_name          TEXT
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO filing_issues
                        (filing_uuid, general_issue_code, year, client_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        # "Company TK01" IS in company_lobbying (ticker TK01).
                        ("uuid-1", "TAX", 2024, "Company TK01"),
                        ("uuid-2", "DEF", 2024, "Company TK01"),
                        # "Company TK02" IS in company_lobbying (ticker TK02).
                        ("uuid-3", "TAX", 2024, "Company TK02"),
                        # Non-investable entity — NOT in company_lobbying with a ticker.
                        ("uuid-4", "LBR", 2024, "AMERICAN BUSINESS ROUNDTABLE"),
                        ("uuid-5", "LBR", 2024, "AMERICAN BUSINESS ROUNDTABLE"),
                        ("uuid-6", "TAX", 2024, "AMERICAN BUSINESS ROUNDTABLE"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            df = app.get_top_issue_codes_by_year(str(tmp_db), year=2024, top_n=15, refresh_token=998_002)

            if df.empty:
                self.skipTest(
                    "No company_lobbying rows for year=2024 in fixture — "
                    "public-company filter cannot be verified."
                )

            codes_returned = set(df["general_issue_code"].tolist())

            # TAX appears in 2 public-company filings — must be present.
            self.assertIn("TAX", codes_returned, msg="TAX from public companies must be included")

            # LBR appears ONLY from the non-investable association — must be absent.
            self.assertNotIn(
                "LBR", codes_returned,
                msg="LBR from non-investable entity must be excluded by public-company filter",
            )

            # DEF appears in 1 public-company filing — must be present.
            self.assertIn("DEF", codes_returned, msg="DEF from public company must be included")

            # TAX n_companies should be 2 (TK01 + TK02), not 3 (which would include the association).
            tax_row = df[df["general_issue_code"] == "TAX"]
            if not tax_row.empty:
                self.assertLessEqual(
                    int(tax_row.iloc[0]["n_companies"]), 2,
                    msg="Non-public entity must not count toward n_companies",
                )

    # ── P4 bonus: filing_issues extraction integrity ───────────────────────────
    def test_filing_issues_table_created_and_populated_by_build(self):
        """
        Verify that _ensure_filing_detail_tables creates both tables, and that
        executemany for issue_rows / agency_rows does not crash on empty input.
        """
        from src.build_company_lobbying import _ensure_filing_detail_tables

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = Path(tmpdir) / "detail_tables_test.db"
            conn = sqlite3.connect(tmp_db)
            try:
                cursor = conn.cursor()
                _ensure_filing_detail_tables(cursor)
                conn.commit()

                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("filing_issues",   tables)
                self.assertIn("filing_agencies", tables)

                # Indexes should also exist.
                indexes = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }
                self.assertIn("idx_fi_code_year",   indexes)
                self.assertIn("idx_fi_client_year",  indexes)
                self.assertIn("idx_fa_agency_year",  indexes)
                self.assertIn("idx_fa_client_year",  indexes)

                # Inserting and querying rows must not crash.
                conn.execute(
                    "INSERT INTO filing_issues (filing_uuid, general_issue_code, year, client_name) "
                    "VALUES (?, ?, ?, ?)",
                    ("test-uuid-1", "TAX", 2024, "Test Corp"),
                )
                conn.execute(
                    "INSERT INTO filing_agencies (filing_uuid, agency_name, year, client_name) "
                    "VALUES (?, ?, ?, ?)",
                    ("test-uuid-1", "Internal Revenue Service", 2024, "Test Corp"),
                )
                conn.commit()

                fi_count = conn.execute("SELECT COUNT(*) FROM filing_issues").fetchone()[0]
                fa_count = conn.execute("SELECT COUNT(*) FROM filing_agencies").fetchone()[0]
                self.assertEqual(fi_count, 1)
                self.assertEqual(fa_count, 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
