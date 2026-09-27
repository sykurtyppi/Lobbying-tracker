"""
Settings tab.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from dashboard.formatting import format_currency
from dashboard.queries import (
    _get_fuzzy_audit,
    get_alias_review_queue,
    get_company_lobbying_export_bytes,
    get_database_stats,
    get_latest_ingestion_status,
    get_nonpositive_spend_rows,
    get_sec_universe_stats,
)
from dashboard.settings import (
    apply_custom_ticker_mappings,
    apply_settings_to_config,
    load_settings,
    save_settings,
)


def render(fetcher, selected_year, available_years):
    """Render the Settings tab."""
    st.markdown("### Settings & Data Management")

    # ── Data Quality Coverage Cards ────────────────────────────────────────
    st.markdown("#### Data Quality Coverage")
    st.caption(
        "Live coverage metrics for the current database. "
        "Refresh the page after a data ingestion to see updated counts."
    )

    try:
        import sqlite3 as _dq_sq3
        _dq_conn = _dq_sq3.connect(fetcher.db_path)
        try:
            # 1. Total company-year rows + ticker match rate
            _dq_base = pd.read_sql_query(
                """
                    SELECT
                        COUNT(*) AS total_rows,
                        SUM(CASE WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN 1 ELSE 0 END) AS matched_rows,
                        SUM(total_lobbying_spend) AS total_spend,
                        SUM(CASE WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN total_lobbying_spend ELSE 0 END) AS matched_spend
                    FROM company_lobbying
                    """,
                _dq_conn,
            ).iloc[0]
            _dq_total  = int(_dq_base["total_rows"] or 0)
            _dq_matched = int(_dq_base["matched_rows"] or 0)
            _dq_spend   = float(_dq_base["total_spend"] or 0)
            _dq_mspend  = float(_dq_base["matched_spend"] or 0)
            _dq_row_pct = round(_dq_matched / _dq_total * 100, 1) if _dq_total else 0
            _dq_spd_pct = round(_dq_mspend / _dq_spend * 100, 1) if _dq_spend else 0

            # 2. Issue code coverage
            _dq_fi_exists = not pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='filing_issues'",
                _dq_conn,
            ).empty
            _dq_fi_rows = int(
                _dq_conn.execute("SELECT COUNT(*) FROM filing_issues").fetchone()[0]
            ) if _dq_fi_exists else 0
            _dq_total_filings = int(
                _dq_conn.execute("SELECT COUNT(*) FROM lobbying_filings").fetchone()[0]
                if pd.read_sql_query(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='lobbying_filings'",
                    _dq_conn,
                ).shape[0] > 0 else 0
            )
            _dq_fi_pct = round(_dq_fi_rows / max(_dq_total_filings, 1) * 100, 1)

            # 3. Agency coverage
            _dq_fa_exists = not pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='filing_agencies'",
                _dq_conn,
            ).empty
            _dq_fa_rows = int(
                _dq_conn.execute("SELECT COUNT(*) FROM filing_agencies").fetchone()[0]
            ) if _dq_fa_exists else 0

            # 4. OpenSecrets
            _dq_os_exists = not pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='opensecrets_contribs'",
                _dq_conn,
            ).empty
            _dq_os_rows = int(
                _dq_conn.execute("SELECT COUNT(*) FROM opensecrets_contribs").fetchone()[0]
            ) if _dq_os_exists else 0

        finally:
            _dq_conn.close()

        _dqc1, _dqc2, _dqc3, _dqc4, _dqc5 = st.columns(5)
        with _dqc1:
            st.metric(
                "Ticker Match Rate",
                f"{_dq_row_pct:.1f}%",
                help=f"{_dq_matched:,} of {_dq_total:,} company-year rows mapped to a public ticker.",
            )
        with _dqc2:
            st.metric(
                "Spend Coverage",
                f"{_dq_spd_pct:.1f}%",
                help=f"${_dq_mspend/1e9:.1f}B of ${_dq_spend/1e9:.1f}B total spend is from ticker-mapped companies.",
            )
        with _dqc3:
            st.metric(
                "Issue Codes",
                f"{_dq_fi_rows:,}" if _dq_fi_rows else "Not populated",
                help=(
                    f"{_dq_fi_rows:,} filing_issues rows ({_dq_fi_pct:.1f}% coverage vs raw filings). "
                    "Run a data refresh to populate."
                    if _dq_fi_rows else
                    "Run a data refresh to populate filing_issues."
                ),
            )
        with _dqc4:
            st.metric(
                "Agency Targets",
                f"{_dq_fa_rows:,}" if _dq_fa_rows else "Not populated",
                help=(
                    f"{_dq_fa_rows:,} filing_agencies rows. Run a data refresh to populate."
                    if _dq_fa_rows else
                    "Run a data refresh to populate filing_agencies."
                ),
            )
        with _dqc5:
            st.metric(
                "OpenSecrets Rows",
                f"{_dq_os_rows:,}" if _dq_os_rows else "Not synced",
                help=(
                    f"{_dq_os_rows:,} rows in opensecrets_contribs."
                    if _dq_os_rows else
                    "Add your OpenSecrets API key below and run a sync."
                ),
            )
    except Exception as _dq_err:
        st.caption(f"Could not load quality metrics: {_dq_err}")

    st.markdown("---")

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
        st.markdown("#### Issue & Agency Data Backfill")
        st.caption(
            "Populate `filing_issues` and `filing_agencies` for a specific year "
            "by re-fetching raw filings from the Senate LDA API. "
            "~60–70% faster than a full rebuild — skips ticker matching, "
            "market-cap lookup, and company_lobbying aggregation."
        )
        _backfill_year = st.selectbox(
            "Year to backfill",
            options=available_years,
            index=0,
            key="s_backfill_year",
        )
        if st.button("Backfill Issue & Agency Data", use_container_width=True):
            with st.spinner(
                f"Re-fetching {_backfill_year} filings from LDA API — "
                "this may take several minutes…"
            ):
                try:
                    from build_company_lobbying import (
                        backfill_filing_detail_tables_for_year,
                    )

                    _bf_result = backfill_filing_detail_tables_for_year(
                        year=int(_backfill_year),
                        db_path=fetcher.db_path,
                    )
                    st.success(
                        f"Backfill complete for **{_backfill_year}**: "
                        f"{_bf_result['filings_fetched']:,} filings fetched → "
                        f"{_bf_result['issue_rows']:,} issue rows, "
                        f"{_bf_result['agency_rows']:,} agency rows written."
                    )
                    st.session_state["cache_buster"] += 1
                except Exception as _bf_err:
                    st.error(f"Backfill error: {_bf_err}")

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
                st.markdown("#### Incomplete Year Data Detected")
                st.warning(
                    f"Year(s) **{', '.join(str(y) for y in sorted(_incomplete))}** "
                    "appear to have an incomplete fetch (the scraper did not reach the "
                    "natural end of the API). Use **'Fetch Data for Selected Year'** in "
                    "the sidebar to force a full re-fetch for those years."
                )
            if _unknown:
                st.markdown("#### Legacy Year Snapshots")
                st.info(
                    f"Year(s) **{', '.join(str(y) for y in sorted(_unknown))}** "
                    "have no ingestion audit metadata yet. Data exists, but completeness "
                    "is unverified until you run a force refresh for those years."
                )
            if not _incomplete and not _unknown:
                st.success("All fetched years have complete data coverage.")
