"""
Top Lobbyists tab.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.charts import create_lobbying_treemap
from dashboard.conviction import get_conviction_scores
from dashboard.formatting import format_currency
from dashboard.leaderboard import (
    get_enriched_leaderboard,
    get_filtered_data,
    get_yoy_growth_leaders,
)
from dashboard.queries import _load_opensecrets_contribs


def render(fetcher, selected_year, selected_quarter, market_cap_filter, sector_filter, min_spend):
    """Render the Top Lobbyists tab."""
    st.markdown(f"### Top Lobbying Spenders — {selected_year}")

    with st.expander("Active Filters", expanded=False):
        st.write(f"**Year:** {selected_year} | **Quarter:** {selected_quarter} | **Min Spend:** ${min_spend}M")
        st.write(f"**Market Cap:** {market_cap_filter or 'All'} | **Sector:** {sector_filter or 'All'}")

    # ── Load base + enrichment data ────────────────────────────────────────
    filtered_df = get_filtered_data(
        fetcher, selected_year, selected_quarter,
        market_cap_filter, sector_filter, min_spend
    )
    enriched_df = get_enriched_leaderboard(
        fetcher.db_path, selected_year,
        refresh_token=st.session_state["cache_buster"]
    )

    # Merge enrichment onto filtered data
    if (
        not filtered_df.empty
        and not enriched_df.empty
        and "entity_key" in filtered_df.columns
    ):
        filtered_df = filtered_df.merge(
            enriched_df, on="entity_key", how="left"
        )
    elif not filtered_df.empty:
        for col in ["yoy_pct", "qoq_pct", "is_spike", "is_consistent", "accel_vs_sector"]:
            filtered_df[col] = None

    st.caption(
        f"{len(filtered_df):,} companies matched your filters "
        + (f"({int(filtered_df['source_rows'].sum()):,} source rows aggregated)"
           if not filtered_df.empty and "source_rows" in filtered_df.columns else "")
    )

    col1, col2 = st.columns([3, 1])

    with col1:
        st.markdown("#### Leaderboard")

        if filtered_df.empty:
            st.warning("No data matches your filters. Try adjusting the filters in the sidebar.")
        else:
            # ── Sort picker ─────────────────────────────────────────────────
            sort_options = {
                "YoY %":          ("yoy_pct",              False),
                "Absolute Spend": ("total_lobbying_spend",  False),
                "QoQ %":          ("qoq_pct",              False),
                "Accel vs Sector":("accel_vs_sector",       False),
                "Spend/MCap":     ("spend_to_mcap_ratio",   False),
            }
            sort_choice = st.selectbox(
                "Sort by",
                list(sort_options.keys()),
                index=0,
                key="tab2_sort",
            )
            sort_col, sort_asc = sort_options[sort_choice]

            # Apply sort (put NaN at bottom)
            work_df = filtered_df.copy()
            if sort_col in work_df.columns:
                work_df = work_df.sort_values(
                    sort_col, ascending=sort_asc, na_position="last"
                ).reset_index(drop=True)

            # ── Build display columns ───────────────────────────────────────
            table_limit = 1000
            disp = work_df.head(table_limit).copy()
            disp["ticker"] = disp["ticker"].fillna("N/A")
            disp["Rank"]        = range(1, len(disp) + 1)
            disp["Company"]     = disp["company_name"]
            disp["Ticker"]      = disp["ticker"]
            disp["Total Spend"] = disp["total_lobbying_spend"].apply(format_currency)
            disp["Market Cap"]  = disp["market_cap"].apply(format_currency)
            disp["Spend/MCap"]  = disp["spend_to_mcap_ratio"].apply(
                lambda x: f"{x*100:.3f}%" if pd.notna(x) else "N/A"
            )
            disp["Sector"]      = disp["sector"].fillna("N/A")

            # YoY % with sign
            def _fmt_pct(v):
                if pd.isna(v):
                    return "N/A"
                return f"{'+' if v >= 0 else ''}{v:.1f}%"

            disp["YoY %"]            = disp["yoy_pct"].apply(_fmt_pct)
            disp["QoQ %"]            = disp["qoq_pct"].apply(_fmt_pct)
            disp["Accel vs Sector"]  = disp["accel_vs_sector"].apply(_fmt_pct)

            # Flag columns as readable text
            def _spike_label(v):
                return "SPIKE" if v else ""

            def _consistent_label(v):
                return "YES" if v else ""

            disp["Spike"]       = disp["is_spike"].apply(_spike_label)
            disp["Consistent"]  = disp["is_consistent"].apply(_consistent_label)

            display_cols = [
                "Rank", "Company", "Ticker", "Total Spend",
                "YoY %", "QoQ %", "Accel vs Sector",
                "Spike", "Consistent",
                "Spend/MCap", "Market Cap", "Sector",
            ]
            st.dataframe(
                disp[display_cols],
                use_container_width=True,
                hide_index=True,
                height=620,
            )
            if len(filtered_df) > table_limit:
                st.caption(
                    f"Showing top {table_limit:,} rows. Download CSV for the full universe."
                )

            # ── Export with enrichment ──────────────────────────────────────
            export_df = work_df[[
                "company_name", "ticker", "total_lobbying_spend",
                "yoy_pct", "qoq_pct", "accel_vs_sector",
                "is_spike", "is_consistent",
                "market_cap", "spend_to_mcap_ratio",
                "sector", "quarters_covered",
            ]].copy()
            export_df["ticker"] = export_df["ticker"].fillna("")
            st.download_button(
                label="Download Leaderboard CSV",
                data=export_df.to_csv(index=False).encode("utf-8"),
                file_name=f"leaderboard_{selected_year}_{selected_quarter}.csv",
                mime="text/csv",
                key="dl_tab2_enriched",
            )

            # ── Lobbying Yield sub-table ────────────────────────────────────
            st.markdown("---")
            st.markdown("#### Top 50 by Spend / Market Cap % (Lobbying Yield)")
            ratio_df = filtered_df[
                filtered_df["spend_to_mcap_ratio"].notna()
                & filtered_df["market_cap"].notna()
            ].copy()
            if not ratio_df.empty:
                ratio_df = ratio_df.sort_values("spend_to_mcap_ratio", ascending=False).head(50)
                ratio_df["ticker"] = ratio_df["ticker"].fillna("N/A")
                ratio_df["Rank"]        = range(1, len(ratio_df) + 1)
                ratio_df["Company"]     = ratio_df["company_name"]
                ratio_df["Ticker"]      = ratio_df["ticker"]
                ratio_df["Total Spend"] = ratio_df["total_lobbying_spend"].apply(format_currency)
                ratio_df["Market Cap"]  = ratio_df["market_cap"].apply(format_currency)
                ratio_df["Spend/MCap %"] = ratio_df["spend_to_mcap_ratio"].apply(
                    lambda x: f"{x*100:.4f}%"
                )
                ratio_df["YoY %"]       = ratio_df["yoy_pct"].apply(_fmt_pct) if "yoy_pct" in ratio_df else "N/A"
                ratio_df["Sector"]      = ratio_df["sector"].fillna("N/A")
                st.dataframe(
                    ratio_df[["Rank", "Company", "Ticker", "Total Spend",
                              "Market Cap", "Spend/MCap %", "YoY %", "Sector"]],
                    use_container_width=True,
                    hide_index=True,
                    height=400,
                )
            else:
                st.info("No companies with market cap data available for this view.")

    with col2:
        st.markdown("#### Summary")
        st.metric("Companies", f"{len(filtered_df):,}")
        if not filtered_df.empty:
            total_spend = filtered_df["total_lobbying_spend"].sum()
            avg_spend   = filtered_df["total_lobbying_spend"].mean()
            st.metric("Total Spend", format_currency(total_spend))
            st.metric("Avg per Company", format_currency(avg_spend))
            if "is_spike" in filtered_df.columns:
                n_spike = int(filtered_df["is_spike"].sum())
                n_cons  = int(filtered_df["is_consistent"].sum())
                st.metric("Spike Flags", f"{n_spike:,}")
                st.metric("Consistent Ramp", f"{n_cons:,}")
        else:
            st.metric("Total Spend", "$0")
            st.metric("Avg per Company", "$0")

        st.markdown("---")
        st.markdown("**Sector Breakdown**")
        if not filtered_df.empty:
            sec_data = (
                filtered_df.groupby("sector")["total_lobbying_spend"]
                .sum()
                .reset_index()
                .nlargest(5, "total_lobbying_spend")
            )
            sec_data.columns = ["Sector", "Spend"]
            fig_pie = go.Figure(data=[go.Pie(
                labels=sec_data["Sector"],
                values=sec_data["Spend"],
                hole=0.4,
                marker=dict(colors=["#60a5fa", "#34d399", "#fbbf24", "#f87171", "#a78bfa"]),
            )])
            fig_pie.update_layout(
                plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                font=dict(color="#ffffff", size=11),
                height=250, showlegend=True,
                margin=dict(l=0, r=0, t=20, b=0),
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No data for sector breakdown.")

        st.markdown("---")
        st.markdown("**Top YoY Growth**")
        growth_df = get_yoy_growth_leaders(
            fetcher.db_path, selected_year,
            refresh_token=st.session_state["cache_buster"],
        )
        if growth_df.empty:
            st.caption(f"Need both {selected_year - 1} and {selected_year} in DB.")
        else:
            for _, row in growth_df.iterrows():
                label = (
                    row["ticker"]
                    if pd.notna(row["ticker"]) and str(row["ticker"]).strip()
                    else row["company_name"][:20]
                )
                sign  = "+" if row["yoy_pct"] >= 0 else ""
                color = "#22c55e" if row["yoy_pct"] >= 0 else "#ef4444"
                st.markdown(
                    f"**{label}**: <span style='color:{color}'>{sign}{row['yoy_pct']:.1f}%</span>",
                    unsafe_allow_html=True,
                )

    # ── Company Detail Drill-down ──────────────────────────────────────────
    st.markdown("---")
    with st.expander("Company / Ticker Detail", expanded=False):
        st.caption(
            "Enter a ticker or company name to see the quarterly spend history "
            "and sector comparison for that entity."
        )
        drill_input = st.text_input(
            "Ticker or Company Name",
            placeholder="e.g. AMZN or Amazon",
            key="tab2_drill_input",
        ).strip().upper()

        if drill_input:
            import sqlite3 as _sq3
            _dc = _sq3.connect(fetcher.db_path)
            try:
                _dq = pd.read_sql_query(
                    """
                        SELECT year, quarter, company_name, ticker,
                               SUM(total_lobbying_spend) AS q_spend
                        FROM company_lobbying
                        WHERE UPPER(ticker) = ?
                           OR UPPER(company_name) LIKE ?
                        GROUP BY year, quarter, company_name, ticker
                        ORDER BY year, quarter
                        """,
                    _dc,
                    params=(drill_input, f"%{drill_input}%"),
                )
                _sp_q = pd.read_sql_query(
                    """
                        SELECT date, return_1m, return_3m, return_6m, return_1y
                        FROM stock_performance
                        WHERE UPPER(ticker) = ?
                        ORDER BY date
                        """,
                    _dc,
                    params=(drill_input,),
                )
            finally:
                _dc.close()

            if _dq.empty:
                st.warning(f"No data found for '{drill_input}'. Check spelling or try the full company name.")
            else:
                co_name = _dq["company_name"].iloc[0]
                co_tick = (
                    _dq["ticker"].dropna().iloc[0]
                    if _dq["ticker"].notna().any()
                    else "N/A"
                )
                st.markdown(f"**{co_name}** ({co_tick})")

                # Quarter-label for x-axis
                _dq["period"] = _dq["year"].astype(str) + " " + _dq["quarter"]

                fig_drill = go.Figure()
                fig_drill.add_trace(go.Bar(
                    x=_dq["period"],
                    y=_dq["q_spend"] / 1_000_000,
                    name="Lobbying Spend ($M)",
                    marker_color="#60a5fa",
                    text=(_dq["q_spend"] / 1_000_000).apply(lambda v: f"${v:.2f}M"),
                    textposition="outside",
                ))
                fig_drill.update_layout(
                    xaxis_title="Quarter",
                    yaxis_title="Spend ($M)",
                    plot_bgcolor="#0e1117",
                    paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    xaxis=dict(gridcolor="#2d3748"),
                    yaxis=dict(gridcolor="#2d3748"),
                    height=380,
                    margin=dict(l=0, r=0, t=10, b=0),
                )
                st.plotly_chart(fig_drill, use_container_width=True)

                # Sector comparison panel
                if not filtered_df.empty and "sector" in _dq.columns:
                    co_sector = (
                        filtered_df.loc[
                            filtered_df["company_name"] == co_name, "sector"
                        ].iloc[0]
                        if co_name in filtered_df["company_name"].values
                        else None
                    )
                    if co_sector and pd.notna(co_sector):
                        sec_peers = filtered_df[
                            filtered_df["sector"] == co_sector
                        ]["total_lobbying_spend"]
                        co_spend = _dq[_dq["year"] == selected_year]["q_spend"].sum()
                        pct_rank = (sec_peers <= co_spend).mean() * 100
                        sec_med  = sec_peers.median()
                        st.markdown(f"**Sector:** {co_sector}")
                        sc1, sc2, sc3 = st.columns(3)
                        sc1.metric("Sector Spend Rank", f"{pct_rank:.0f}th pct.")
                        sc2.metric("Sector Median Spend", format_currency(sec_med))
                        sc3.metric(f"{selected_year} Spend", format_currency(co_spend))

                # Stock return snapshot if available
                if not _sp_q.empty:
                    st.markdown("**Stock Performance Snapshots**")
                    _sp_q["date"] = pd.to_datetime(_sp_q["date"]).dt.date
                    st.dataframe(
                        _sp_q.rename(columns={
                            "date": "Date",
                            "return_1m": "1M Return (%)",
                            "return_3m": "3M Return (%)",
                            "return_6m": "6M Return (%)",
                            "return_1y": "1Y Return (%)",
                        }),
                        use_container_width=True,
                        hide_index=True,
                    )

    # ── Full-width: Treemap ────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Lobbying Spend Treemap by Sector")
    st.caption(
        "Relative block size = total lobbying spend. "
        "Click a sector block to zoom in on individual companies."
    )
    _tm_df = filtered_df[
        filtered_df["sector"].notna() & (filtered_df["total_lobbying_spend"] > 0)
    ]
    if _tm_df.empty:
        st.info(
            "No sector data available for treemap. "
            "Run a sector backfill first (Advanced: Enrich Existing Data → Refresh Sectors)."
        )
    else:
        st.plotly_chart(
            create_lobbying_treemap(_tm_df, selected_year),
            use_container_width=True,
        )

    # ── OpenSecrets Cross-Reference ────────────────────────────────────────
    with st.expander("OpenSecrets Cross-Reference (Political Contributions)", expanded=False):
        st.caption(
            "PAC contributions and political spending data from OpenSecrets, "
            "synced via Settings → OpenSecrets API. Requires an API key."
        )
        _os_data = _load_opensecrets_contribs(
            fetcher.db_path, selected_year, st.session_state["cache_buster"]
        )
        if _os_data.empty:
            st.info(
                "No OpenSecrets data yet for this year. "
                "Go to **Settings → OpenSecrets API** to add your API key and run a sync."
            )
        else:
            st.dataframe(
                _os_data.rename(columns={
                    "company_name":  "Company",
                    "ticker":        "Ticker",
                    "total_contribs":"Total Contribs ($)",
                    "pacs":          "PAC ($)",
                    "indivs":        "Individual ($)",
                    "os_lobbying":   "OS Lobbying ($)",
                    "outside_spend": "Outside Spend ($)",
                }),
                use_container_width=True,
                hide_index=True,
                height=350,
            )
            st.caption(
                f"Showing {len(_os_data)} companies. "
                "Contributions are for the full election cycle, not calendar year."
            )

    # ── Conviction Score Leaderboard ──────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Conviction Score Leaderboard")
    st.caption(
        "Composite score (max 100) combining four signals: "
        "**YoY spend growth** (0–40 pts, sqrt-curve), "
        "**Spend/Market-Cap yield** (0–25 pts, percentile rank), "
        "**Filing consistency** across quarters (0–25 pts), "
        "and **PAC + political contributions** from OpenSecrets (0–10 pts, if available). "
        "Only public-company tickers are included. "
        "Used internally by the backtest engine to rank portfolio candidates."
    )

    with st.spinner("Computing conviction scores..."):
        conv_df = get_conviction_scores(
            fetcher.db_path,
            selected_year,
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
        )

    if conv_df.empty:
        st.info(
            f"No conviction scores available for {selected_year}. "
            "Ensure data for this year and the prior year are loaded, "
            "and that at least some companies are mapped to tickers."
        )
    else:
        conv_disp = conv_df.copy().head(200)
        conv_disp["Rank"]            = range(1, len(conv_disp) + 1)
        conv_disp["Company"]         = conv_disp["company_name"]
        conv_disp["Ticker"]          = conv_disp["ticker"]
        conv_disp["Score"]           = conv_disp["conviction_score"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else "N/A"
        )
        conv_disp["YoY Pts (0-40)"]  = conv_disp["yoy_pts"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else "0.0"
        )
        conv_disp["MCap Pts (0-25)"] = conv_disp["mcap_pts"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else "0.0"
        )
        conv_disp["Cons Pts (0-25)"] = conv_disp["consistency_pts"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else "0.0"
        )
        conv_disp["OS Pts (0-10)"]   = conv_disp["os_pts"].apply(
            lambda v: f"{v:.1f}" if pd.notna(v) else "0.0"
        )
        conv_disp["Sector"]          = conv_disp["sector"].fillna("N/A") if "sector" in conv_disp.columns else "N/A"

        _conv_display_cols = [
            "Rank", "Company", "Ticker", "Score",
            "YoY Pts (0-40)", "MCap Pts (0-25)", "Cons Pts (0-25)", "OS Pts (0-10)",
            "Sector",
        ]
        # Only include columns that actually exist in the dataframe
        _conv_display_cols = [c for c in _conv_display_cols if c in conv_disp.columns]

        st.dataframe(
            conv_disp[_conv_display_cols],
            use_container_width=True,
            hide_index=True,
            height=480,
        )
        if len(conv_df) > 200:
            st.caption(
                f"Showing top 200 of {len(conv_df):,} scored companies. "
                "Download CSV for the full universe."
            )

        # Show whether OS data boosted any scores
        os_active = (conv_df["os_pts"] > 0).sum()
        if os_active > 0:
            st.caption(
                f"OpenSecrets data active: {os_active:,} companies received OS bonus pts "
                f"(max boost: {conv_df['os_pts'].max():.1f} pts)."
            )
        else:
            st.caption(
                "OpenSecrets data not yet loaded — OS Pts column shows 0.0 for all companies. "
                "Add your API key in **Settings → OpenSecrets API** to enable."
            )

        st.download_button(
            label="Download Conviction Scores CSV",
            data=conv_df[[
                "company_name", "ticker", "conviction_score",
                "yoy_pts", "mcap_pts", "consistency_pts", "os_pts",
                "yoy_pct", "quarters_count",
            ]].to_csv(index=False).encode("utf-8"),
            file_name=f"conviction_scores_{selected_year}.csv",
            mime="text/csv",
            key="dl_conviction_scores",
        )
