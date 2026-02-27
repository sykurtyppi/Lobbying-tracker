import atexit
import shutil
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

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

    @patch("app._fetch_spx_returns_for_signal_years")
    def test_benchmark_year_alignment_and_cost_adjustment(self, mock_spx):
        # Deterministic SPX map; values are not material for this test.
        mock_spx.return_value = ({yr: 0.0 for yr in range(2018, 2030)}, None)

        df = app.get_benchmark_comparison(str(DB_PATH), refresh_token=999001)
        if df.empty:
            self.skipTest("No benchmark rows available in fixture DB.")

        self.assertTrue(
            {
                "signal_year",
                "year",
                "strategy_return_gross",
                "strategy_return",
                "turnover_pct",
                "cost_applied_pct",
            }.issubset(df.columns)
        )
        self.assertTrue(((df["signal_year"] + 1) == df["year"]).all())

        cost_bps = float(getattr(app.config, "BACKTEST_REBALANCE_COST_BPS", 0.0) or 0.0)
        expected_cost = (
            (df["turnover_pct"] / 100.0) * (cost_bps / 100.0)
        ).round(4)
        self.assertTrue((df["cost_applied_pct"].round(4) == expected_cost).all())

        # strategy_return is rounded to 2dp in app code, so compare at 2dp
        deltas = (df["strategy_return_gross"] - df["strategy_return"]).round(2)
        self.assertTrue((deltas == df["cost_applied_pct"].round(2)).all())

    @patch("app._fetch_spx_returns_for_signal_years")
    def test_new_entrant_hold_year_alignment(self, mock_spx):
        mock_spx.return_value = ({yr: 0.0 for yr in range(2018, 2030)}, None)

        _, bt_df = app.get_new_entrant_signal(
            str(DB_PATH),
            min_curr_spend_m=1.0,
            max_prev_spend_k=200.0,
            refresh_token=999002,
        )
        if bt_df.empty:
            self.skipTest("No new-entrant backtest rows available in fixture DB.")

        self.assertTrue({"signal_year", "year", "avg_return_gross", "avg_return"}.issubset(bt_df.columns))
        self.assertTrue(((bt_df["signal_year"] + 1) == bt_df["year"]).all())

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

    def test_signal_returns_are_ticker_deduped(self):
        conn = sqlite3.connect(DB_PATH)
        try:
            year_row = conn.execute(
                """
                SELECT MAX(year)
                FROM company_lobbying
                WHERE quarter = 'Q4'
                """
            ).fetchone()
        finally:
            conn.close()

        year = int(year_row[0]) if year_row and year_row[0] is not None else None
        if year is None:
            self.skipTest("No Q4 rows found in company_lobbying.")

        df = app.get_signal_returns(str(DB_PATH), year, refresh_token=999003)
        if df.empty:
            self.skipTest(f"No signal returns rows for {year}.")

        tickers = df["ticker"].dropna().astype(str).str.strip().str.upper()
        tickers = tickers[tickers != ""]
        self.assertEqual(int(tickers.nunique()), int(len(tickers)))

    def test_acceleration_features_have_expected_shape(self):
        conn = sqlite3.connect(DB_PATH)
        try:
            year_row = conn.execute(
                """
                SELECT MAX(year)
                FROM (
                    SELECT year, COUNT(DISTINCT quarter) AS n_quarters
                    FROM company_lobbying
                    GROUP BY year
                )
                WHERE n_quarters = 4
                """
            ).fetchone()
        finally:
            conn.close()

        signal_year = int(year_row[0]) if year_row and year_row[0] is not None else None
        if signal_year is None:
            self.skipTest("No complete filing year found.")

        df = app.get_acceleration_features(
            str(DB_PATH),
            year=signal_year,
            refresh_token=999004,
            min_spend_m=1.0,
            ticker_only=True,
        )
        if df.empty:
            self.skipTest(f"No acceleration feature rows for {signal_year}.")

        required_cols = {
            "ticker",
            "company_name",
            "sector",
            "signal_year",
            "annual_spend",
            "yoy_pct",
            "qoq_pct",
            "hist_spend_z",
            "sector_spike_z",
            "mcap_ratio_yoy_pct",
            "feature_count",
            "accel_score",
        }
        self.assertTrue(required_cols.issubset(df.columns))

        scored = df[df["accel_score"].notna()].copy()
        if not scored.empty:
            self.assertTrue(((scored["accel_score"] >= 0) & (scored["accel_score"] <= 100)).all())
            tickers = scored["ticker"].dropna().astype(str).str.strip().str.upper()
            tickers = tickers[tickers != ""]
            self.assertEqual(int(tickers.nunique()), int(len(tickers)))

    @patch("app._fetch_spx_returns_for_signal_years")
    def test_acceleration_backtest_year_alignment(self, mock_spx):
        mock_spx.return_value = ({yr: 0.0 for yr in range(2018, 2035)}, None)

        bt_df, _ = app.get_acceleration_event_backtest(
            str(DB_PATH),
            top_n=20,
            refresh_token=999005,
            min_spend_m=1.0,
        )
        if bt_df.empty:
            self.skipTest("No acceleration backtest rows available.")

        self.assertTrue({"signal_year", "year", "alpha_return_3m", "alpha_return_6m", "alpha_return_1y"}.issubset(bt_df.columns))
        self.assertTrue(((bt_df["signal_year"] + 1) == bt_df["year"]).all())

        # Internal consistency: alpha = top - universe for each horizon where both exist.
        for horizon in ["3m", "6m", "1y"]:
            top_col = f"top_return_{horizon}"
            uni_col = f"universe_return_{horizon}"
            alpha_col = f"alpha_return_{horizon}"
            valid = bt_df[top_col].notna() & bt_df[uni_col].notna() & bt_df[alpha_col].notna()
            if valid.any():
                calc = (bt_df.loc[valid, top_col] - bt_df.loc[valid, uni_col]).round(2)
                self.assertTrue((calc == bt_df.loc[valid, alpha_col].round(2)).all())

    @patch("app._fetch_spx_returns_for_signal_years")
    def test_acceleration_rolling_windows_have_expected_shape(self, mock_spx):
        mock_spx.return_value = ({yr: 0.0 for yr in range(2018, 2035)}, None)

        roll_df = app.get_acceleration_rolling_windows(
            str(DB_PATH),
            top_n=20,
            window_years=3,
            refresh_token=999006,
            min_spend_m=1.0,
        )
        if roll_df.empty:
            self.skipTest("No rolling-window rows available.")

        self.assertTrue(
            {"window_start", "window_end", "window_label", "n_years", "avg_alpha_1y"}.issubset(
                roll_df.columns
            )
        )
        self.assertTrue((roll_df["n_years"] >= 3).all())
        self.assertTrue((roll_df["window_start"] <= roll_df["window_end"]).all())

    def test_acceleration_oos_split_returns_train_test_artifacts(self):
        annual_df, summary_df, coef_df = app.get_acceleration_oos_split_backtest(
            str(DB_PATH),
            top_n=20,
            refresh_token=999007,
            min_spend_m=1.0,
            train_start=2019,
            train_end=2022,
            test_start=2023,
            test_end=2026,
        )
        if annual_df.empty:
            self.skipTest("No OOS split rows available.")

        self.assertTrue({"signal_year", "year", "split", "alpha_return_1y"}.issubset(annual_df.columns))
        self.assertTrue(((annual_df["signal_year"] + 1) == annual_df["year"]).all())
        self.assertIn("Train", set(annual_df["split"].tolist()))
        self.assertFalse(coef_df.empty)
        self.assertTrue({"feature", "weight"}.issubset(coef_df.columns))
        if not summary_df.empty:
            self.assertIn("Split", summary_df.columns)

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


if __name__ == "__main__":
    unittest.main()
