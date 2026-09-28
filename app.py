"""
US Lobbying Disclosure Tracker - Dashboard
Explore federal lobbying filings by company, issue, and quarter

Entry point only. Analytics live in ``dashboard/`` (one module per concern)
and each tab renders from ``dashboard/tabs/``. The public helpers are
re-exported here so ``import app`` keeps working for tests and scripts.
"""

import streamlit as st

import dashboard  # noqa: F401  (puts repo root and src/ on sys.path)
import config  # noqa: F401  (re-exported for callers that use app.config)
from dashboard.sidebar import render_sidebar
from dashboard.tabs import (
    company_research,
    overview,
    settings_tab,
    top_lobbyists,
)

# Re-exports: keep the historical ``app.<helper>`` surface stable.
from dashboard.charts import (  # noqa: F401
    create_lobbying_treemap,
)
from dashboard.company import (  # noqa: F401
    get_company_annual_spend,
    get_company_entity_names,
    get_company_lobbyist_firms,
    get_company_sector_peers,
    get_company_stock_snapshots,
    search_companies,
)
from dashboard.conviction import (  # noqa: F401
    get_conviction_scores,
)
from dashboard.formatting import (  # noqa: F401
    format_currency,
)
from dashboard.leaderboard import (  # noqa: F401
    aggregate_entities,
    get_enriched_leaderboard,
    get_filtered_data,
    get_yearly_summary,
    get_yoy_growth_leaders,
)
from dashboard.queries import (  # noqa: F401
    _REPO_ROOT,
    _get_fuzzy_audit,
    _get_revalidation_preview,
    _load_opensecrets_contribs,
    get_alias_review_queue,
    get_available_sectors,
    get_available_years,
    get_company_lobbying_export_bytes,
    get_data_fetcher,
    get_database_stats,
    get_default_year,
    get_issue_breakdown_for_ticker,
    get_latest_ingestion_status,
    get_nonpositive_spend_rows,
    get_sec_universe_stats,
    get_top_issue_codes_by_year,
)
from dashboard.sector_signals import (  # noqa: F401
    _SECTOR_ETF_MAP,
    get_sector_spend_by_year,
)
from dashboard.settings import (  # noqa: F401
    _REPO_ROOT,
    _SETTINGS_CONFIG_MAP,
    _SETTINGS_PATH,
    _config_defaults,
    apply_custom_ticker_mappings,
    apply_settings_to_config,
    load_settings,
    save_settings,
)
from dashboard.theme import (  # noqa: F401
    _APP_CSS,
    _configure_streamlit_page,
)


def main():
    _configure_streamlit_page()

    if "cache_buster" not in st.session_state:
        st.session_state["cache_buster"] = 0

    # Header
    st.markdown("<h1 style='text-align: center;'>US Lobbying Disclosure Tracker</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #a0a0a0;'>Who is lobbying, on what, and how it changes quarter to quarter</p>", unsafe_allow_html=True)
    
    # Initialize tools
    fetcher = get_data_fetcher()

    # ── Startup schema migration check ──────────────────────────────────────
    # Ensures filing_issues, filing_agencies, and schema_version tables exist.
    # Streamlit reruns often; run this check once per session.
    if not st.session_state.get("_schema_check_ran", False):
        try:
            from build_company_lobbying import ensure_schema_current
            _migration_result = ensure_schema_current(fetcher.db_path)
            if _migration_result["migrated"]:
                st.warning(
                    "**Database schema updated automatically** — "
                    f"applied migration(s): {_migration_result['migrations_applied']}. "
                    "This happens once after updating the app. "
                    "Go to **Settings → Issue & Agency Data Backfill** to populate any "
                    "newly created tables without running a full data rebuild."
                )
        except Exception as _schema_err:
            st.warning(f"Schema check failed (non-critical): {_schema_err}")
        finally:
            st.session_state["_schema_check_ran"] = True

    # Get years that actually have data
    available_years = get_available_years(fetcher.db_path, st.session_state["cache_buster"])
    default_year = get_default_year(fetcher.db_path, st.session_state["cache_buster"])
    try:
        default_year_index = available_years.index(default_year)
    except ValueError:
        default_year_index = 0

    state = render_sidebar(fetcher, available_years, default_year_index)
    selected_year = state.selected_year
    selected_quarter = state.selected_quarter
    market_cap_filter = state.market_cap_filter
    sector_filter = state.sector_filter
    min_spend = state.min_spend
    selected_year_complete = state.selected_year_complete
    selected_ingestion_status = state.selected_ingestion_status

    # Main content tabs
    if selected_year_complete is False:
        st.error(
            f"Selected year {selected_year} is not marked complete (last run status: {selected_ingestion_status}). "
            "Signals may be biased by partial data until this year is fully refreshed."
        )

    tab1, tab2, tab3, tab4 = st.tabs([
        "Overview", "Top Lobbyists", "Company Research", "Settings"
    ])

    with tab1:
        overview.render(fetcher, selected_year, selected_quarter, market_cap_filter, sector_filter, min_spend)

    with tab2:
        top_lobbyists.render(fetcher, selected_year, selected_quarter, market_cap_filter, sector_filter, min_spend)

    with tab3:
        company_research.render(fetcher)

    with tab4:
        settings_tab.render(fetcher, selected_year, available_years)

    
    # Footer
    st.markdown("---")
    st.markdown(
        "<p style='text-align: center; color: #a0a0a0; font-size: 12px;'>"
        "Data sources: Senate Lobbying Disclosure, OpenSecrets.org, Yahoo Finance | "
        "Built on public lobbying expenditure filings | "
        "For informational purposes only"
        "</p>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
