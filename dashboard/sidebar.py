"""
Sidebar controls: year/quarter/market-cap/sector filters, data refresh, and DB status.
"""

import sys
import os
import time
import sqlite3

import pandas as pd
import streamlit as st

import config

from dashboard.queries import (
    _get_revalidation_preview,
    get_available_sectors,
    get_latest_ingestion_status,
)


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


from dataclasses import dataclass


@dataclass(frozen=True)
class SidebarState:
    """Filter selections made in the sidebar, consumed by every tab."""
    selected_year: object
    selected_quarter: object
    market_cap_filter: object
    sector_filter: object
    min_spend: object
    selected_year_complete: object
    selected_ingestion_status: object


def render_sidebar(fetcher, available_years, default_year_index):
    """Render sidebar controls and return the selected filter state."""
    selected_year_complete = None
    selected_ingestion_status = None
    with st.sidebar:
        st.markdown("###  Controls")
        
        selected_year = st.selectbox(
            "Year",
            options=available_years,
            index=default_year_index
        )
        
        selected_quarter = st.selectbox(
            "Quarter",
            options=['Q1', 'Q2', 'Q3', 'Q4', 'YTD', 'Full Year'],
            index=5  # Default to Full Year
        )
        
        market_cap_filter = st.multiselect(
            "Market Cap",
            options=['Large Cap (>$10B)', 'Mid Cap ($2B-$10B)', 'Small Cap (<$2B)'],
            default=['Large Cap (>$10B)', 'Mid Cap ($2B-$10B)', 'Small Cap (<$2B)']
        )
        
        available_sectors = get_available_sectors(fetcher.db_path, st.session_state["cache_buster"])
        sector_filter = st.multiselect(
            "Sector",
            options=available_sectors,
            default=[]
        )
        
        st.markdown("---")
        
        min_spend = st.number_input(
            "Min Lobbying Spend ($M)",
            min_value=0.0,
            max_value=100.0,
            value=1.0,
            step=0.5
        )
        
        st.markdown("---")
        
        if st.button(" Refresh Data", use_container_width=True):
            st.session_state["cache_buster"] += 1
            st.rerun()
        
        st.markdown("---")
        
        # Add pipeline refresh button
        with st.expander("Advanced: Fetch / Refresh Data"):
            st.markdown("**Fetch latest lobbying filings from Senate database**")
            st.info(
                f"This will **fully re-fetch** all filings for {selected_year} and rebuild the "
                f"database for that year. Use this to fix incomplete years (e.g. 2024 fetched "
                f"mid-year) or to pull the latest filings. May take 5–15 minutes."
            )
            
            if st.button(" Fetch Data for Selected Year", use_container_width=True):
                with st.spinner(f"Fetching {selected_year} data from Senate database..."):
                    try:
                        import sys
                        import os
                        sys.path.append(os.path.join(_REPO_ROOT, 'src'))
                        from build_company_lobbying import build_company_lobbying_for_year
                        
                        # Run the pipeline in the UI
                        build_company_lobbying_for_year(
                            selected_year,
                            db_path=fetcher.db_path,
                            force=True,
                        )
                        
                        st.success(f"Successfully fetched and processed {selected_year} data.")
                        st.info("Refreshing app with new data...")
                        
                        # Bump refresh token and reload
                        st.session_state["cache_buster"] += 1
                        time.sleep(2)
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f" Error fetching data: {e}")
                        st.info("You can also run the pipeline from command line: `python src/build_company_lobbying.py`")
        
        st.markdown("---")

        with st.expander("Advanced: Enrich Existing Data"):
            st.markdown("**Backfill ticker & market-cap for unmatched companies**")
            st.caption(
                "Runs the improved CompanyMapper (with fuzzy matching) over all rows "
                "that currently have no ticker — no Senate API call required."
            )
            if st.button("Run Ticker Backfill", use_container_width=True):
                with st.spinner("Scanning unmatched companies…"):
                    try:
                        from build_company_lobbying import backfill_ticker_mappings
                        result = backfill_ticker_mappings(db_path=fetcher.db_path)
                        st.success(
                            f"Backfill complete: checked {result['checked']:,} names, "
                            f"matched {result['matched']:,} new tickers."
                        )
                        method_counts = result.get("method_counts") or {}
                        if method_counts:
                            method_df = pd.DataFrame(
                                [
                                    {"Match Method": m, "Count": c}
                                    for m, c in sorted(
                                        method_counts.items(),
                                        key=lambda x: (-x[1], x[0]),
                                    )
                                ]
                            )
                            st.caption("Backfill match-method breakdown")
                            st.dataframe(
                                method_df,
                                use_container_width=True,
                                hide_index=True,
                                height=min(220, 40 + 35 * len(method_df)),
                            )
                        st.session_state["cache_buster"] += 1
                    except Exception as e:
                        st.error(f"Backfill error: {e}")

            st.markdown("---")
            st.markdown("**Revalidate existing ticker assignments**")
            st.caption(
                "Re-checks every ticker in the database against the current stricter "
                "CompanyMapper (0.82 cutoff + token-overlap guard). Stale false-positive "
                "fuzzy matches from the old 0.72-era logic are nullified."
            )

            # ── Step 1: Preview (always safe, read-only) ─────────────────────
            if st.button("Preview Changes (dry-run)", use_container_width=True, key="rv_preview"):
                with st.spinner("Evaluating all existing assignments…"):
                    preview_df = _get_revalidation_preview(
                        fetcher.db_path, st.session_state["cache_buster"]
                    )
                st.session_state["rv_preview_df"] = preview_df

            if "rv_preview_df" in st.session_state:
                preview_df = st.session_state["rv_preview_df"]
                if preview_df.empty:
                    st.success("All existing ticker assignments look correct — nothing to change.")
                else:
                    nullify_n = (preview_df["Action"] == "NULLIFY").sum()
                    remap_n   = (preview_df["Action"] == "REMAP").sum()
                    st.warning(
                        f"**{nullify_n:,} assignments would be nullified** "
                        f"(bad fuzzy matches) · **{remap_n:,} would be remapped** "
                        f"(corrected ticker). Review below before committing."
                    )

                    # Colour-code by action for readability
                    action_filter = st.selectbox(
                        "Show",
                        ["All changes", "NULLIFY only", "REMAP only"],
                        key="rv_action_filter",
                    )
                    show_df = preview_df.copy()
                    if action_filter == "NULLIFY only":
                        show_df = show_df[show_df["Action"] == "NULLIFY"]
                    elif action_filter == "REMAP only":
                        show_df = show_df[show_df["Action"] == "REMAP"]

                    st.dataframe(show_df, use_container_width=True, hide_index=True, height=350)
                    st.download_button(
                        "Download Preview CSV",
                        data=preview_df.to_csv(index=False).encode("utf-8"),
                        file_name="revalidation_preview.csv",
                        mime="text/csv",
                        key="dl_rv_preview",
                    )

                    st.markdown("---")
                    st.markdown(
                        "**Ready to commit?** Inspect the list above.  \n"
                        "Legitimate matches that shouldn't be nullified should be added "
                        "to **Settings → Custom Ticker Mappings** before running — "
                        "they'll be persisted in `entity_aliases` and preserved by lookup."
                    )
                    # ── Step 2: Commit button (write) ─────────────────────────
                    if st.button(
                        f"Commit — nullify {nullify_n:,} + remap {remap_n:,}",
                        use_container_width=True,
                        type="primary",
                        key="rv_commit",
                    ):
                        with st.spinner("Writing changes to database…"):
                            try:
                                from build_company_lobbying import revalidate_ticker_mappings
                                rv = revalidate_ticker_mappings(
                                    db_path=fetcher.db_path, dry_run=False
                                )
                                st.success(
                                    f"Done — nullified {rv['nullified']:,}, "
                                    f"remapped {rv['changed']:,}, "
                                    f"unchanged {rv['unchanged']:,}."
                                )
                                remap_methods = rv.get("remap_method_counts") or {}
                                if remap_methods:
                                    remap_df = pd.DataFrame(
                                        [
                                            {"Remap Method": m, "Count": c}
                                            for m, c in sorted(
                                                remap_methods.items(),
                                                key=lambda x: (-x[1], x[0]),
                                            )
                                        ]
                                    )
                                    st.caption("Revalidation remap-method breakdown")
                                    st.dataframe(
                                        remap_df,
                                        use_container_width=True,
                                        hide_index=True,
                                        height=min(180, 40 + 35 * len(remap_df)),
                                    )
                                # Clear cached preview so next run re-reads DB
                                del st.session_state["rv_preview_df"]
                                st.session_state["cache_buster"] += 1
                            except Exception as e:
                                st.error(f"Revalidation error: {e}")

            st.markdown("---")
            st.markdown("**Populate stock performance table**")
            st.caption(
                "For every matched ticker, fetches price history and calculates "
                "forward returns (1m, 3m, 6m, 1y) anchored to lag-adjusted "
                "quarter signals. Run this after a backfill to unlock signal validation."
            )
            st.caption(
                f"Current filing lag assumption: {int(getattr(config, 'LDA_FILING_LAG_DAYS', 20))} day(s). "
                "If you change this in Settings, repopulate stock performance."
            )
            lookback = st.slider("Years of history", min_value=1, max_value=7, value=7, key="perf_lookback")
            if st.button("Populate Stock Performance", use_container_width=True):
                with st.spinner("Fetching price history from Yahoo Finance… this may take several minutes."):
                    try:
                        from build_company_lobbying import populate_stock_performance
                        result = populate_stock_performance(db_path=fetcher.db_path, lookback_years=lookback)
                        st.success(
                            f"Done: {result['tickers_processed']} tickers, "
                            f"{result['rows_inserted']} rows inserted, "
                            f"{result['errors']} errors."
                        )
                        st.session_state["cache_buster"] += 1
                    except Exception as e:
                        st.error(f"Error: {e}")

        st.markdown("---")
        
        # Data availability indicator
        st.markdown("####  Data Status")
        ingestion_df = get_latest_ingestion_status(
            fetcher.db_path, st.session_state["cache_buster"]
        )
        
        # Get data counts
        conn = sqlite3.connect(fetcher.db_path)
        
        # Check what we have for selected year
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM company_lobbying WHERE year = ?", (selected_year,))
        year_count = cursor.fetchone()[0]
        
        # Check quarters available
        cursor.execute("SELECT DISTINCT quarter FROM company_lobbying WHERE year = ? ORDER BY quarter", (selected_year,))
        quarters = [row[0] for row in cursor.fetchall()]
        
        # Total companies
        cursor.execute("SELECT COUNT(DISTINCT company_name) FROM company_lobbying")
        total_companies = cursor.fetchone()[0]
        
        conn.close()
        
        if year_count > 0:
            run_row = ingestion_df[ingestion_df["year"] == selected_year] if not ingestion_df.empty else pd.DataFrame()
            if not run_row.empty:
                row = run_row.iloc[0]
                status = str(row.get("status") or "unknown")
                is_complete_raw = row.get("is_complete")
                selected_ingestion_status = status

                if pd.isna(is_complete_raw):
                    selected_year_complete = None
                    st.warning(
                        f"{selected_year}: {year_count:,} records "
                        f"(status: {status.replace('_', ' ')})"
                    )
                    st.caption(
                        "No ingestion audit row for this year yet. "
                        "Run a force refresh once to populate ingestion metadata."
                    )
                elif int(is_complete_raw) == 1:
                    selected_year_complete = True
                    st.success(f"{selected_year}: {year_count:,} records (ingestion complete)")
                else:
                    selected_year_complete = False
                    st.error(
                        f"{selected_year}: {year_count:,} records (last run: {status})"
                    )
                    st.caption("Re-fetch this year via 'Advanced: Fetch / Refresh Data' above.")

                api_count = row.get("api_reported_count")
                fetched = row.get("fetched_count")
                if pd.notna(api_count) and pd.notna(fetched):
                    st.caption(f"API reported {int(api_count):,} filings · fetched {int(fetched):,}")
                notes = row.get("notes")
                if isinstance(notes, str) and notes.strip() and notes.strip().lower() != "complete":
                    st.caption(f"Run notes: {notes}")
                latest_attempt = row.get("latest_attempt_status")
                if (
                    isinstance(latest_attempt, str)
                    and latest_attempt.strip()
                    and latest_attempt != status
                ):
                    st.caption(
                        f"Latest attempt status: {latest_attempt} "
                        "(snapshot status shown above)"
                    )
            else:
                selected_year_complete = None
                selected_ingestion_status = None
                if year_count < 10_000:
                    st.warning(f"{selected_year}: {year_count:,} records (status unknown)")
                    st.caption("Run a force refresh once to create ingestion metadata for this year.")
                else:
                    st.success(f"{selected_year}: {year_count:,} records")
            if quarters:
                st.caption(f"Quarters: {', '.join(quarters)}")
        else:
            st.warning(f"{selected_year}: No data yet")
            st.caption("Click 'Fetch / Refresh Data' above to load it")
        
        st.metric("Total Companies in DB", f"{total_companies:,}")
        
        st.markdown("---")
        
        st.markdown("#### Data Sources")
        st.markdown("- Senate Lobbying Disclosure")
        st.markdown("- OpenSecrets.org")
        st.markdown("- Yahoo Finance")

    return SidebarState(
        selected_year=selected_year,
        selected_quarter=selected_quarter,
        market_cap_filter=market_cap_filter,
        sector_filter=sector_filter,
        min_spend=min_spend,
        selected_year_complete=selected_year_complete,
        selected_ingestion_status=selected_ingestion_status,
    )
