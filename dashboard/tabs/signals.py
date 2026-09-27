"""
Signals tab.
"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config

from dashboard.acceleration import get_production_strategy_holdings
from dashboard.formatting import format_currency
from dashboard.quality import fetch_quality_metrics_for_tickers, get_quality_metrics_from_db
from dashboard.queries import get_top_issue_codes_by_year
from dashboard.sector_signals import (
    get_new_entrant_signal,
    get_sector_rotation_signal,
    get_sector_spend_by_year,
)


def render(fetcher, selected_year, min_spend):
    """Render the Signals tab."""
    st.markdown("### Research Signals")
    st.caption(
        "Three experimental signals derived from lobbying data. "
        "Results improve significantly as more historical years are loaded — "
        "fetch 2019–2022 from the sidebar to extend the backtest window."
    )

    sig_s1, sig_s2, sig_s3 = st.tabs([
        "Sector Rotation", "New Entrant Detection", "Quality Gate (Production)"
    ])

    # ── Signal 1: Sector Rotation ──────────────────────────────────────
    with sig_s1:
        st.markdown("#### Sector Rotation Signal")
        st.caption(
            "Logic: sectors that ramp lobbying spend the most in year Y are "
            "hypothesised to be seeking regulatory tailwinds. "
            "The signal buys the corresponding SPDR sector ETF at the close of "
            "year Y and holds for 12 months (year Y+1 return)."
        )

        with st.spinner("Loading sector rotation data..."):
            sr_df = get_sector_rotation_signal(
                fetcher.db_path, refresh_token=st.session_state["cache_buster"]
            )
            sector_long_df = get_sector_spend_by_year(
                fetcher.db_path, refresh_token=st.session_state["cache_buster"]
            )

        if sr_df.empty:
            st.info(
                "Not enough data to compute sector rotation signal. "
                "At least two years of lobbying data are required."
            )
        else:
            # ── Sector spend ramp chart (selected year) ────────────────
            if not sector_long_df.empty:
                st.markdown("**Sector Lobbying Spend Ramp — Selected Year**")
                yr_sectors = sector_long_df[
                    sector_long_df["year"] == selected_year
                ].copy()
                if not yr_sectors.empty and yr_sectors["yoy_pct"].notna().any():
                    yr_sectors = yr_sectors.sort_values("yoy_pct", ascending=True)
                    fig_sr_bar = go.Figure()
                    colors_sr = [
                        "#22c55e" if v >= 0 else "#ef4444"
                        for v in yr_sectors["yoy_pct"]
                    ]
                    fig_sr_bar.add_trace(go.Bar(
                        x=yr_sectors["yoy_pct"],
                        y=yr_sectors["sector"],
                        orientation="h",
                        marker_color=colors_sr,
                        text=[
                            f"{v:+.1f}%" if pd.notna(v) else "N/A"
                            for v in yr_sectors["yoy_pct"]
                        ],
                        textposition="outside",
                        customdata=yr_sectors[["etf", "spend"]].values,
                        hovertemplate=(
                            "<b>%{y}</b><br>"
                            "ETF: %{customdata[0]}<br>"
                            "YoY: %{x:+.1f}%<br>"
                            "Spend: $%{customdata[1]:,.0f}<extra></extra>"
                        ),
                    ))
                    fig_sr_bar.update_layout(
                        title=f"Sector Lobbying YoY% — {selected_year}",
                        xaxis_title="YoY% Change in Lobbying Spend",
                        plot_bgcolor="#0e1117",
                        paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        height=max(300, len(yr_sectors) * 35),
                        margin=dict(l=0, r=60, t=45, b=0),
                    )
                    st.plotly_chart(fig_sr_bar, use_container_width=True,
                                    key="sr_bar_chart")
                else:
                    st.info(f"No prior-year sector data for {selected_year} to compute YoY%.")

            st.markdown("---")
            st.markdown("**Backtest: Top-Ramping Sector ETF vs S&P 500**")
            st.caption(
                "Each row: the sector that ramped lobbying most in 'year', "
                "its SPDR ETF, and the ETF's actual calendar-year return "
                "for the *following* year (the holding period)."
            )

            # KPIs
            valid_sr = sr_df[sr_df["etf_return"].notna() & sr_df["spx_return"].notna()]
            if not valid_sr.empty:
                avg_etf = valid_sr["etf_return"].mean()
                avg_spx = valid_sr["spx_return"].mean()
                avg_alpha = valid_sr["alpha"].mean() if valid_sr["alpha"].notna().any() else None
                win_rate = (valid_sr["alpha"] > 0).mean() * 100

                ksr1, ksr2, ksr3, ksr4 = st.columns(4)
                with ksr1:
                    st.metric("Years Tracked", f"{len(valid_sr)}")
                with ksr2:
                    st.metric("Avg ETF Return", f"{avg_etf:+.1f}%")
                with ksr3:
                    st.metric("Avg S&P 500 Return", f"{avg_spx:+.1f}%")
                with ksr4:
                    color_label = f"{avg_alpha:+.1f}%" if avg_alpha is not None else "N/A"
                    st.metric("Avg Alpha", color_label)

            # Chart
            if not valid_sr.empty:
                fig_sr = go.Figure()
                sr_years = valid_sr["year"].tolist()
                fig_sr.add_trace(go.Bar(
                    x=sr_years,
                    y=valid_sr["etf_return"].tolist(),
                    name="Sector ETF (hold yr)",
                    marker_color="#60a5fa",
                    text=[f"{v:+.1f}%" for v in valid_sr["etf_return"]],
                    textposition="outside",
                ))
                fig_sr.add_trace(go.Bar(
                    x=sr_years,
                    y=valid_sr["spx_return"].tolist(),
                    name="S&P 500",
                    marker_color="#f97316",
                    text=[f"{v:+.1f}%" for v in valid_sr["spx_return"]],
                    textposition="outside",
                ))
                fig_sr.add_hline(y=0, line_color="#6b7280", line_width=1)
                fig_sr.update_layout(
                    barmode="group",
                    xaxis=dict(title="Signal Year", dtick=1,
                               gridcolor="#2d3748", tickvals=sr_years),
                    yaxis=dict(title="1-Year Return (%) — following year",
                               gridcolor="#2d3748"),
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                borderwidth=1, orientation="h",
                                yanchor="bottom", y=1.02, xanchor="right", x=1),
                    height=380,
                    margin=dict(l=0, r=40, t=50, b=0),
                )
                st.plotly_chart(fig_sr, use_container_width=True,
                                key="sr_backtest_chart")

            # Table
            display_sr = sr_df.copy()
            display_sr = display_sr.rename(columns={
                "year": "Signal Year",
                "top_sector": "Top Sector",
                "etf_ticker": "ETF",
                "sector_yoy_pct": "Sector YoY%",
                "n_sectors": "Sectors Ranked",
                "etf_return": "ETF Return (hold yr)",
                "spx_return": "S&P 500 (hold yr)",
                "alpha": "Alpha",
            })

            def _pct(v):
                return f"{v:+.1f}%" if pd.notna(v) else "N/A"

            for col in ["Sector YoY%", "ETF Return (hold yr)", "S&P 500 (hold yr)", "Alpha"]:
                if col in display_sr.columns:
                    display_sr[col] = display_sr[col].apply(_pct)

            st.dataframe(display_sr, use_container_width=True, hide_index=True)

            if "_fetch_error" in sr_df.columns:
                st.warning(f"ETF data fetch error: {sr_df['_fetch_error'].iloc[0]}")

    # ── Signal 2: New Entrant Detection ───────────────────────────────
    with sig_s2:
        st.markdown("#### New Entrant / Spend Acceleration Signal")
        st.caption(
            "Flags companies whose lobbying spend crosses from near-zero to "
            "significant in a single year. The thesis: a company deploying "
            "material lobbying capital for the first time signals strategic "
            "regulatory engagement, which may precede regulatory wins or "
            "contract awards not yet priced in."
        )
        st.caption(
            "Backtest year is hold year (forward 12M return). "
            "Signal comes from the prior filing year."
        )

        ne_c1, ne_c2 = st.columns(2)
        with ne_c1:
            ne_min_curr = st.number_input(
                "Min current-year spend ($M)",
                min_value=0.5, max_value=10.0, value=1.0, step=0.5,
                key="ne_min_curr",
            )
        with ne_c2:
            ne_max_prev = st.number_input(
                "Max prior-year spend ($K)",
                min_value=0.0, max_value=500.0, value=200.0, step=50.0,
                key="ne_max_prev",
            )

        with st.spinner("Loading new entrant data..."):
            ne_detail_df, ne_bt_df = get_new_entrant_signal(
                fetcher.db_path,
                min_curr_spend_m=ne_min_curr,
                max_prev_spend_k=ne_max_prev,
                refresh_token=st.session_state["cache_buster"],
            )

        if ne_bt_df.empty:
            st.info(
                "No new entrant backtest data available. "
                "Stock performance data must be populated first "
                "(use 'Populate Stock Performance' in Settings)."
            )
        else:
            valid_ne = ne_bt_df[
                ne_bt_df["avg_return"].notna() & ne_bt_df["spx_return"].notna()
            ]
            if not valid_ne.empty:
                avg_ne_ret = valid_ne["avg_return"].mean()
                avg_ne_spx = valid_ne["spx_return"].mean()
                avg_ne_alpha = valid_ne["alpha"].dropna().mean()
                total_entrants = ne_detail_df["company_name"].nunique() if not ne_detail_df.empty else 0

                kne1, kne2, kne3, kne4 = st.columns(4)
                with kne1:
                    st.metric("Years in Backtest", f"{len(valid_ne)}")
                with kne2:
                    st.metric("Unique New Entrants", f"{total_entrants}")
                with kne3:
                    st.metric("Avg New Entrant Return", f"{avg_ne_ret:+.1f}%")
                with kne4:
                    ne_alpha_str = f"{avg_ne_alpha:+.1f}%" if pd.notna(avg_ne_alpha) else "N/A"
                    st.metric("Avg Alpha vs S&P 500", ne_alpha_str)

            # Backtest chart
            if not valid_ne.empty:
                fig_ne = go.Figure()
                ne_years = valid_ne["year"].tolist()
                fig_ne.add_trace(go.Bar(
                    x=ne_years,
                    y=valid_ne["avg_return"].tolist(),
                    name="New Entrants (equal-weight)",
                    marker_color="#a78bfa",
                    text=[f"{v:+.1f}%" for v in valid_ne["avg_return"]],
                    textposition="outside",
                    customdata=valid_ne["n_with_returns"].tolist(),
                    hovertemplate=(
                        "Hold Year %{x}<br>"
                        "New Entrant Return: %{y:+.1f}%<br>"
                        "N companies: %{customdata}<extra></extra>"
                    ),
                ))
                fig_ne.add_trace(go.Bar(
                    x=ne_years,
                    y=valid_ne["spx_return"].tolist(),
                    name="S&P 500",
                    marker_color="#f97316",
                    text=[f"{v:+.1f}%" for v in valid_ne["spx_return"]],
                    textposition="outside",
                ))
                fig_ne.add_hline(y=0, line_color="#6b7280", line_width=1)
                fig_ne.update_layout(
                    barmode="group",
                    title="New Entrant 1-Year Forward Returns vs S&P 500",
                    xaxis=dict(title="Hold Year", dtick=1, gridcolor="#2d3748",
                               tickvals=ne_years),
                    yaxis=dict(title="Equal-Weight 1Y Return (%)",
                               gridcolor="#2d3748"),
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                borderwidth=1, orientation="h",
                                yanchor="bottom", y=1.02, xanchor="right", x=1),
                    height=380,
                    margin=dict(l=0, r=40, t=50, b=0),
                )
                st.plotly_chart(fig_ne, use_container_width=True,
                                key="ne_backtest_chart")

            # Backtest table
            st.markdown("**Annual Backtest Summary**")
            bt_show = ne_bt_df.copy()
            bt_show = bt_show.rename(columns={
                "signal_year": "Signal Year",
                "year": "Hold Year",
                "n_entrants": "New Entrants",
                "n_with_returns": "W/ Returns",
                "avg_return": "Avg Return",
                "spx_return": "S&P 500",
                "alpha": "Alpha",
            })
            for c in ["Avg Return", "S&P 500", "Alpha"]:
                if c in bt_show.columns:
                    bt_show[c] = bt_show[c].apply(
                        lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                    )
            st.dataframe(bt_show, use_container_width=True,
                         hide_index=True, height=220)

        # Detail for selected year
        if not ne_detail_df.empty:
            st.markdown(f"---")
            st.markdown(f"**New Entrants in {selected_year}**")
            yr_ne = ne_detail_df[
                ne_detail_df["signal_year"] == selected_year
            ].copy()
            if yr_ne.empty:
                st.info(f"No new entrants detected for {selected_year} under current thresholds.")
            else:
                yr_ne["curr_spend"] = yr_ne["curr_spend"].apply(format_currency)
                yr_ne["prev_spend"] = yr_ne["prev_spend"].apply(format_currency)
                yr_ne["return_1y"] = yr_ne["return_1y"].apply(
                    lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                )
                yr_ne = yr_ne.rename(columns={
                    "company_name": "Company",
                    "ticker": "Ticker",
                    "sector": "Sector",
                    "curr_spend": "Current Yr Spend",
                    "prev_spend": "Prior Yr Spend",
                    "return_1y": "1Y Fwd Return",
                })
                st.dataframe(
                    yr_ne.drop(columns=["year"], errors="ignore"),
                    use_container_width=True, hide_index=True,
                )

    # ── Signal 3: Quality Gate on Production Picks ──────────────────────
    with sig_s3:
        st.markdown("#### Quality-Gated Production Picks")
        st.caption(
            "Applies a quality gate to the production watchlist "
            f"(signal year {int(selected_year)} -> hold year {int(selected_year) + 1}). "
            "This keeps the screen aligned with the live model instead of a separate score."
        )

        # Quality filter controls
        qf_col1, qf_col2, qf_col3 = st.columns(3)
        with qf_col1:
            qf_min_roe = st.number_input(
                "Min ROE (%)",
                min_value=-50.0,
                max_value=100.0,
                value=5.0,
                step=1.0,
                key="qf_min_roe",
                help="Return on equity (proxy for ROIC). Set to -50 to disable.",
            )
        with qf_col2:
            qf_max_de = st.number_input(
                "Max Debt/Equity Ratio (x)",
                min_value=0.0,
                max_value=20.0,
                value=3.0,
                step=0.5,
                key="qf_max_de",
                help=(
                    "Maximum debt-to-equity ratio. 3x means total debt "
                    "is no more than 3x equity. Set to 20 to disable."
                ),
            )
        with qf_col3:
            qf_pos_eg = st.checkbox(
                "Require positive earnings growth",
                value=True,
                key="qf_pos_eg",
            )

        prod_top_n_cfg = int(getattr(config, "PRODUCTION_TOP_N", 10))
        prod_min_usable_cfg = int(getattr(config, "PRODUCTION_MIN_USABLE_NAMES", 5))
        prod_primary_factor_cfg = str(
            getattr(config, "PRODUCTION_PRIMARY_FACTOR", "hist_spend_z")
        )
        prod_fallback_factor_cfg = str(
            getattr(config, "PRODUCTION_FALLBACK_FACTOR", "accel_score")
        )
        prod_hold_year_qf = int(selected_year) + 1

        prod_screen_df, prod_meta_qf = get_production_strategy_holdings(
            fetcher.db_path,
            hold_year=prod_hold_year_qf,
            top_n=prod_top_n_cfg,
            primary_factor=prod_primary_factor_cfg,
            fallback_factor=prod_fallback_factor_cfg,
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
            min_usable_names=prod_min_usable_cfg,
        )

        if prod_screen_df.empty:
            st.info(
                f"No production picks available for signal year {selected_year} "
                f"(hold year {prod_hold_year_qf})."
            )
        else:
            prod_screen_df = prod_screen_df.copy().reset_index(drop=True)
            prod_screen_df["production_rank"] = prod_screen_df.index + 1

            quality_df = get_quality_metrics_from_db(
                fetcher.db_path, refresh_token=st.session_state["cache_buster"]
            )

            tickers_in_screen = (
                prod_screen_df["ticker"].dropna().astype(str).str.strip().unique().tolist()
            )
            already_fetched = (
                quality_df["ticker"].astype(str).str.strip().tolist()
                if not quality_df.empty
                else []
            )
            stale_cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            stale_tickers = []
            if not quality_df.empty:
                stale_mask = quality_df["fetched_at"] < stale_cutoff
                stale_tickers = (
                    quality_df.loc[stale_mask, "ticker"]
                    .astype(str)
                    .str.strip()
                    .tolist()
                )
            to_fetch_list = [
                t for t in tickers_in_screen if t not in already_fetched or t in stale_tickers
            ]

            qf_btn_col, qf_info_col = st.columns([1, 3])
            with qf_btn_col:
                run_qf_fetch = st.button(
                    f"Fetch Quality Data ({len(to_fetch_list)} tickers)",
                    use_container_width=True,
                    key="qf_fetch_btn",
                    disabled=(len(to_fetch_list) == 0),
                )
            with qf_info_col:
                if len(to_fetch_list) == 0:
                    st.caption(
                        "Quality data is current for this production watchlist. "
                        f"Model in use: {prod_meta_qf.get('model_used', 'N/A')}."
                    )
                else:
                    st.caption(
                        f"{len(to_fetch_list)} ticker(s) need quality data. "
                        "Fetch pulls ROE, D/E, and earnings growth from Yahoo Finance "
                        "for this production list."
                    )

            if run_qf_fetch:
                with st.spinner(
                    f"Fetching quality metrics for {len(to_fetch_list)} tickers..."
                ):
                    fetch_quality_metrics_for_tickers(to_fetch_list, fetcher.db_path)
                st.session_state["cache_buster"] += 1
                quality_df = get_quality_metrics_from_db(
                    fetcher.db_path, refresh_token=st.session_state["cache_buster"]
                )
                st.success(f"Fetched quality data for {len(to_fetch_list)} tickers.")

            if quality_df.empty:
                st.info(
                    "No quality data in database yet. "
                    "Click 'Fetch Quality Data' above to populate ROE, D/E, and "
                    "earnings growth for production picks."
                )
            else:
                merged_qf = prod_screen_df.merge(
                    quality_df[["ticker", "roe", "debt_to_equity", "earnings_growth", "fetched_at"]],
                    on="ticker",
                    how="left",
                )

                # Apply quality filters.
                mask = pd.Series([True] * len(merged_qf), index=merged_qf.index)
                roe_has_data = merged_qf["roe"].notna()
                if qf_min_roe > -50:
                    mask &= (~roe_has_data) | (merged_qf["roe"] * 100 >= qf_min_roe)

                de_has_data = merged_qf["debt_to_equity"].notna()
                # Yahoo debtToEquity is percentage-point units (e.g. 175 = 1.75x).
                mask &= (~de_has_data) | (merged_qf["debt_to_equity"] / 100 <= qf_max_de)

                if qf_pos_eg:
                    eg_has_data = merged_qf["earnings_growth"].notna()
                    mask &= (~eg_has_data) | (merged_qf["earnings_growth"] > 0)

                filtered_qf = merged_qf[mask].copy()
                unfiltered_count = len(merged_qf)
                filtered_count = len(filtered_qf)

                kqf1, kqf2, kqf3, kqf4 = st.columns(4)
                with kqf1:
                    st.metric("Before Filter", f"{unfiltered_count}")
                with kqf2:
                    st.metric("After Filter", f"{filtered_count}")
                with kqf3:
                    st.metric("Removed", f"{unfiltered_count - filtered_count}")
                with kqf4:
                    pct_pass = (
                        filtered_count / unfiltered_count * 100
                        if unfiltered_count > 0
                        else 0
                    )
                    st.metric("Pass Rate", f"{pct_pass:.0f}%")

                st.markdown("**Quality-Filtered Production Screen**")
                st.caption(
                    "Rows with missing quality fields are kept (shown as —), "
                    "so the production list remains complete while highlighting "
                    "where manual review is needed."
                )

                disp_qf = filtered_qf.copy().sort_values("production_rank").head(50)
                disp_qf["annual_spend"] = disp_qf["annual_spend"].apply(format_currency)
                disp_qf["hist_spend_z"] = disp_qf["hist_spend_z"].apply(
                    lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                )
                disp_qf["accel_score"] = disp_qf["accel_score"].apply(
                    lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                )
                disp_qf["roe_pct"] = disp_qf["roe"].apply(
                    lambda v: f"{v * 100:.1f}%" if pd.notna(v) else "—"
                )
                disp_qf["de_fmt"] = disp_qf["debt_to_equity"].apply(
                    lambda v: f"{v / 100:.2f}x" if pd.notna(v) else "—"
                )
                disp_qf["eg_fmt"] = disp_qf["earnings_growth"].apply(
                    lambda v: f"{v * 100:+.1f}%" if pd.notna(v) else "—"
                )

                show_cols = {
                    "production_rank": "Rank",
                    "company_name": "Company",
                    "ticker": "Ticker",
                    "sector": "Sector",
                    "annual_spend": "Spend",
                    "hist_spend_z": "Hist Z",
                    "accel_score": "Composite",
                    "roe_pct": "ROE",
                    "de_fmt": "D/E",
                    "eg_fmt": "Earnings Growth",
                    "fetched_at": "Data Date",
                }
                disp_qf = disp_qf.rename(
                    columns={k: v for k, v in show_cols.items() if k in disp_qf.columns}
                )
                display_columns = [v for v in show_cols.values() if v in disp_qf.columns]
                st.dataframe(disp_qf[display_columns], use_container_width=True, hide_index=True)

                st.download_button(
                    "Download Quality-Filtered Production CSV",
                    data=filtered_qf.to_csv(index=False).encode("utf-8"),
                    file_name=f"quality_filtered_production_{selected_year}.csv",
                    mime="text/csv",
                    key="dl_qf",
                )

    # ── Market-Wide Issue Pulse ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Market Issue Pulse")
    st.caption(
        "Which policy areas are drawing the most lobbying activity among "
        "ticker-mapped (investable) companies this year? "
        "Non-public entities — trade associations, NGOs, government bodies — "
        "are excluded so counts reflect the investable universe only. "
        "Issue codes follow the Senate LDA 76-code taxonomy. "
        "Research (Lowry & Volkova 2024) shows agency-targeted lobbying "
        "(DEF, FIN, ENV) yields 30–70% higher firm value uplift vs Congress-only lobbying. "
        "Requires a data refresh to populate (filing_issues table)."
    )

    _mip_year = selected_year
    with st.spinner("Loading market issue data..."):
        mip_df = get_top_issue_codes_by_year(
            fetcher.db_path,
            year=_mip_year,
            top_n=15,
            refresh_token=st.session_state["cache_buster"],
        )

    if mip_df.empty:
        st.info(
            f"No issue-code data yet for {_mip_year}. "
            "Trigger a **Data Refresh** from the sidebar to populate the "
            "`filing_issues` table from the Senate LDA API."
        )
    else:
        _MIP_LABELS = {
            "TAX": "Taxation", "DEF": "Defense", "HCR": "Health Issues",
            "FIN": "Financial/Securities", "ENV": "Environment",
            "LBR": "Labor/Antitrust", "TEC": "Telecommunications",
            "CPT": "Copyright/Patent", "FOR": "Foreign Relations",
            "TRD": "Trade", "BUD": "Budget/Appropriations",
            "MMM": "Medicare/Medicaid", "PHA": "Pharmacy",
            "MED": "Medical Research", "EDU": "Education",
            "HOM": "Homeland Security", "INT": "Intelligence",
            "FUE": "Fuel/Gas/Oil", "UTI": "Utilities",
            "COM": "Communications", "AVI": "Aviation/Airlines",
            "TRA": "Transportation", "INS": "Insurance",
            "BAN": "Banking", "IMM": "Immigration",
            "LAW": "Law Enforcement",
        }
        mip_df["issue_label"] = (
            mip_df["general_issue_code"]
            .map(_MIP_LABELS)
            .fillna(mip_df["general_issue_code"])
        )
        mip_df_sorted = mip_df.sort_values("n_activities", ascending=True)

        _mip_colors = [
            "#ef4444" if code in ("TAX", "DEF", "FIN", "HCR")
            else "#3b82f6"
            for code in mip_df_sorted["general_issue_code"]
        ]

        fig_mip = go.Figure()
        fig_mip.add_trace(go.Bar(
            x=mip_df_sorted["n_activities"],
            y=mip_df_sorted["issue_label"],
            orientation="h",
            marker_color=_mip_colors,
            text=[f"{int(v):,}" for v in mip_df_sorted["n_activities"]],
            textposition="outside",
            customdata=mip_df_sorted[["n_companies", "general_issue_code"]].values,
            hovertemplate=(
                "<b>%{y}</b> (%{customdata[1]})<br>"
                "Activities: %{x:,}<br>"
                "Companies: %{customdata[0]:,}<extra></extra>"
            ),
        ))
        fig_mip.update_layout(
            title=f"Top Policy Issue Areas — {_mip_year} (red = high-alpha codes per research)",
            xaxis=dict(title="Filing Activities", gridcolor="#2d3748"),
            yaxis=dict(title="", gridcolor="#2d3748"),
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font=dict(color="#ffffff", size=12),
            height=max(350, len(mip_df) * 28),
            margin=dict(l=0, r=80, t=50, b=0),
        )
        st.plotly_chart(fig_mip, use_container_width=True, key="mip_chart")

        # Summary table
        mip_tbl = mip_df.sort_values("n_activities", ascending=False).copy()
        mip_tbl["Issue Code"] = mip_tbl["general_issue_code"]
        mip_tbl["Issue Area"] = mip_tbl["issue_label"]
        mip_tbl["Activities"] = mip_tbl["n_activities"].apply(lambda v: f"{int(v):,}")
        mip_tbl["Companies"]  = mip_tbl["n_companies"].apply(lambda v: f"{int(v):,}")
        st.dataframe(
            mip_tbl[["Issue Code", "Issue Area", "Activities", "Companies"]],
            use_container_width=True,
            hide_index=True,
            key="mip_table",
        )
