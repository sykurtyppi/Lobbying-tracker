"""
Overview tab.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.formatting import format_currency
from dashboard.leaderboard import get_filtered_data, get_yearly_summary
from dashboard.sector_signals import get_sector_spend_by_year


def render(fetcher, selected_year, selected_quarter, market_cap_filter, sector_filter, min_spend):
    """Render the Overview tab."""
    st.markdown("### Coverage Metrics")

    # Get filtered data first so metrics are grounded in live DB values.
    holdings_df = get_filtered_data(
        fetcher, selected_year, selected_quarter, market_cap_filter, sector_filter, min_spend
    )

    total_companies = len(holdings_df)
    total_spend = holdings_df["total_lobbying_spend"].sum() if total_companies > 0 else 0
    median_spend = holdings_df["total_lobbying_spend"].median() if total_companies > 0 else 0
    ticker_coverage = (
        (holdings_df["ticker"].fillna("").str.strip() != "").mean() * 100
        if total_companies > 0
        else 0
    )
    mcap_coverage = (
        holdings_df["market_cap"].notna().mean() * 100 if total_companies > 0 else 0
    )

    st.caption("Live metrics from your database for the currently selected filters.")

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Companies", f"{total_companies:,}")
    with col2:
        st.metric("Total Lobbying Spend", format_currency(total_spend))
    with col3:
        st.metric("Median Spend", format_currency(median_spend))
    with col4:
        st.metric("Ticker Coverage", f"{ticker_coverage:.1f}%")
    with col5:
        st.metric("Market Cap Coverage", f"{mcap_coverage:.1f}%")

    st.markdown("---")

    # ── Data Trust Panel ──────────────────────────────────────────────────
    with st.expander("Data Quality", expanded=False):
        import sqlite3 as _dt_sq3
        _dt_conn = _dt_sq3.connect(fetcher.db_path)
        try:
            _dt_total = pd.read_sql_query(
                """
                    SELECT COUNT(*) AS n
                    FROM company_lobbying
                    WHERE year = ?
                      AND total_lobbying_spend > 0
                    """,
                _dt_conn, params=(selected_year,)
            ).iloc[0]["n"]
            _dt_mapped = pd.read_sql_query(
                """
                    SELECT COUNT(*) AS n
                    FROM company_lobbying
                    WHERE year = ?
                      AND total_lobbying_spend > 0
                      AND ticker IS NOT NULL
                      AND ticker != ''
                    """,
                _dt_conn, params=(selected_year,)
            ).iloc[0]["n"]
            _dt_unmatched = pd.read_sql_query(
                """
                    SELECT COUNT(DISTINCT company_name) AS n
                    FROM company_lobbying
                    WHERE year = ?
                      AND total_lobbying_spend > 0
                      AND (ticker IS NULL OR ticker = '')
                    """,
                _dt_conn, params=(selected_year,)
            ).iloc[0]["n"]
            _dt_last_run = pd.read_sql_query(
                """SELECT MAX(COALESCE(finished_at, started_at)) AS last_run
                       FROM ingestion_runs WHERE year = ?""",
                _dt_conn, params=(selected_year,)
            ).iloc[0]["last_run"]
            if _dt_last_run is None:
                _dt_last_run = pd.read_sql_query(
                    """SELECT MAX(last_updated) AS last_run
                           FROM company_lobbying WHERE year = ?""",
                    _dt_conn,
                    params=(selected_year,),
                ).iloc[0]["last_run"]
            # New filings ingested in last 7 days
            _week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            _dt_new_week = pd.read_sql_query(
                """SELECT COUNT(*) AS n FROM ingestion_runs
                       WHERE year = ? AND started_at >= ?""",
                _dt_conn, params=(selected_year, _week_ago)
            ).iloc[0]["n"]
        except Exception:
            _dt_total = _dt_mapped = _dt_unmatched = 0
            _dt_last_run = None
            _dt_new_week = 0
        finally:
            _dt_conn.close()

        _dt_match_rate = (_dt_mapped / _dt_total * 100) if _dt_total > 0 else 0.0
        dt1, dt2, dt3, dt4 = st.columns(4)
        dt1.metric(
            "Ticker Match Rate",
            f"{_dt_match_rate:.1f}%",
            help="Rows with a mapped public ticker ÷ total rows for this year.",
        )
        dt2.metric(
            "Unmatched Entities",
            f"{_dt_unmatched:,}",
            help="Distinct company names with no ticker match for this year.",
        )
        dt3.metric(
            "Ingestion Runs (7d)",
            f"{_dt_new_week}",
            help="Number of data fetch runs completed in the last 7 days.",
        )
        dt4.metric(
            "Last Refresh",
            str(_dt_last_run)[:16] if _dt_last_run else "Never",
            help="Timestamp of the most recent completed ingestion run for this year.",
        )
        if _dt_unmatched > 0:
            st.caption(
                f"{_dt_unmatched:,} company names have no ticker mapping. "
                "Use Settings → Custom Ticker Mappings to fill gaps."
            )

    st.markdown("---")

    yearly_summary = get_yearly_summary(fetcher.db_path, st.session_state["cache_buster"])
    if yearly_summary.empty:
        st.info("No historical company-level data available yet.")
    else:
        # Keep only completed full years (exclude current in-progress year).
        full_year_cutoff = datetime.now().year - 1
        ys = yearly_summary[yearly_summary["year"] <= full_year_cutoff].copy()
        if ys.empty:
            ys = yearly_summary.copy()
        ys_years = ys["year"].tolist()

        # Compute YoY% on total spend
        ys["yoy_pct"] = ys["total_lobbying_spend"].pct_change().multiply(100).round(1)

        # ── Chart: Spend bars + YoY% line + Company count ─────────────
        st.markdown("#### Total Lobbying Spend — All Years")
        summary_chart = go.Figure()

        summary_chart.add_trace(go.Bar(
            x=ys_years,
            y=(ys["total_lobbying_spend"] / 1_000_000).round(1).tolist(),
            name="Total Spend ($M)",
            marker_color="#60a5fa",
            text=(ys["total_lobbying_spend"] / 1_000_000).round(1).apply(
                lambda v: f"${v:.0f}M"
            ).tolist(),
            textposition="outside",
            hovertemplate="Year %{x}<br>Spend: $%{y:.1f}M<extra></extra>",
        ))

        # YoY% line on secondary axis
        valid_yoy = ys["yoy_pct"].notna()
        if valid_yoy.any():
            yoy_colors = [
                "#22c55e" if (pd.notna(v) and v >= 0) else "#ef4444"
                for v in ys["yoy_pct"]
            ]
            summary_chart.add_trace(go.Scatter(
                x=ys_years,
                y=ys["yoy_pct"].tolist(),
                name="YoY% Change",
                mode="lines+markers+text",
                line=dict(color="#f97316", width=2.5, dash="dot"),
                marker=dict(size=8, color=yoy_colors),
                text=[
                    f"{v:+.1f}%" if pd.notna(v) else ""
                    for v in ys["yoy_pct"]
                ],
                textposition="top center",
                textfont=dict(size=11),
                yaxis="y2",
                hovertemplate="YoY: %{y:+.1f}%<extra></extra>",
            ))

        summary_chart.add_hline(
            y=0, line_color="#6b7280", line_width=1, line_dash="solid",
            annotation=None,
        )
        summary_chart.update_layout(
            xaxis=dict(
                title="Year", dtick=1, tickvals=ys_years,
                gridcolor="#2d3748", showgrid=True,
            ),
            yaxis=dict(
                title="Total Spend ($M)",
                gridcolor="#2d3748", showgrid=True,
                tickformat="$,.0f",
            ),
            yaxis2=dict(
                title="YoY% Change",
                overlaying="y", side="right",
                showgrid=False,
                zeroline=True, zerolinecolor="#6b7280",
            ),
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font=dict(color="#ffffff", size=12),
            legend=dict(
                bgcolor="#1a1d24", bordercolor="#4a5568", borderwidth=1,
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="right", x=1,
            ),
            height=420,
            margin=dict(l=0, r=60, t=50, b=0),
            barmode="group",
        )
        st.plotly_chart(summary_chart, use_container_width=True,
                        key="overview_annual_chart")

        # ── KPI row: coverage stats across all years ───────────────────
        ov_c1, ov_c2, ov_c3, ov_c4, ov_c5 = st.columns(5)
        with ov_c1:
            st.metric("Years of Data", f"{len(ys_years)}")
        with ov_c2:
            st.metric(
                "Total Spend (all years)",
                f"${ys['total_lobbying_spend'].sum()/1e9:.1f}B",
            )
        with ov_c3:
            peak_yr = int(ys.loc[ys["total_lobbying_spend"].idxmax(), "year"])
            st.metric("Peak Spend Year", str(peak_yr))
        with ov_c4:
            avg_yoy = ys["yoy_pct"].dropna().mean()
            st.metric(
                "Avg Annual Growth",
                f"{avg_yoy:+.1f}%" if pd.notna(avg_yoy) else "N/A",
            )
        with ov_c5:
            latest_companies = int(ys.iloc[-1]["unique_entities"])
            st.metric("Companies (latest yr)", f"{latest_companies:,}")

        # ── Sector spend trend (completed years) ───────────────────────
        st.markdown("---")
        st.markdown("#### Sector Lobbying Spend Trend")
        st.caption(
            "Annual lobbying spend by sector across all years. "
            "Highlights which industries are increasing regulatory engagement over time."
        )
        sector_trend_df = get_sector_spend_by_year(
            fetcher.db_path,
            refresh_token=st.session_state["cache_buster"],
        )
        if not sector_trend_df.empty:
            sector_trend_df = sector_trend_df[
                sector_trend_df["year"] <= (datetime.now().year - 1)
            ]
            sector_palette = [
                "#60a5fa", "#f97316", "#22c55e", "#a78bfa",
                "#f43f5e", "#34d399", "#fbbf24", "#38bdf8",
                "#e879f9", "#fb923c", "#84cc16",
            ]
            sectors_ordered = (
                sector_trend_df.groupby("sector")["spend"]
                .sum()
                .sort_values(ascending=False)
                .index.tolist()
            )
            fig_sec_trend = go.Figure()
            for i, sec in enumerate(sectors_ordered):
                sec_data = sector_trend_df[sector_trend_df["sector"] == sec]
                fig_sec_trend.add_trace(go.Scatter(
                    x=sec_data["year"].tolist(),
                    y=(sec_data["spend"] / 1_000_000).round(1).tolist(),
                    name=sec,
                    mode="lines+markers",
                    line=dict(
                        color=sector_palette[i % len(sector_palette)],
                        width=2,
                    ),
                    marker=dict(size=6),
                    hovertemplate=(
                        f"{sec}<br>Year %{{x}}: $%{{y:.1f}}M<extra></extra>"
                    ),
                ))
            fig_sec_trend.update_layout(
                xaxis=dict(
                    title="Year", dtick=1,
                    tickvals=sorted(sector_trend_df["year"].unique().tolist()),
                    gridcolor="#2d3748",
                ),
                yaxis=dict(
                    title="Annual Spend ($M)",
                    gridcolor="#2d3748",
                    tickformat="$,.0f",
                ),
                plot_bgcolor="#0e1117",
                paper_bgcolor="#0e1117",
                font=dict(color="#ffffff", size=12),
                legend=dict(
                    bgcolor="#1a1d24", bordercolor="#4a5568", borderwidth=1,
                    orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1,
                ),
                height=420,
                margin=dict(l=0, r=40, t=50, b=0),
            )
            st.plotly_chart(fig_sec_trend, use_container_width=True,
                            key="overview_sector_trend")
        
    # Companies matching the current filters
    st.markdown("### Companies Matching Filters")

    if len(holdings_df) == 0:
        st.warning("No companies match your current filters. Adjust filters in the sidebar.")
    else:
        # Top Lobbying Yield highlight
        valid_ratios = holdings_df[holdings_df['spend_to_mcap_ratio'].notna() & (holdings_df['market_cap'].notna())]
            
        if len(valid_ratios) > 0:
            best_row = valid_ratios.sort_values('spend_to_mcap_ratio', ascending=False).iloc[0]
                
            st.markdown("####  Top Lobbying Yield (Spend / Market Cap)")
                
            col_a, col_b, col_c, col_d = st.columns(4)
            with col_a:
                st.metric("Company", best_row['company_name'][:25] + "..." if len(best_row['company_name']) > 25 else best_row['company_name'])
            with col_b:
                best_ticker = (
                    best_row["ticker"]
                    if pd.notna(best_row["ticker"]) and str(best_row["ticker"]).strip()
                    else "N/A"
                )
                st.metric("Ticker", best_ticker)
            with col_c:
                st.metric("Spend/MCap %", f"{best_row['spend_to_mcap_ratio']*100:.4f}%" if pd.notna(best_row['spend_to_mcap_ratio']) else "N/A")
            with col_d:
                st.metric("Lobbying Spend", format_currency(best_row['total_lobbying_spend']))
                
            st.markdown("---")
            
        # Show top companies
        display_df = holdings_df.head(20).copy()
        display_df["ticker"] = display_df["ticker"].fillna("N/A")
        display_df['Lobbying Spend'] = display_df['total_lobbying_spend'].apply(format_currency)
        display_df['Market Cap'] = display_df['market_cap'].apply(format_currency)
        display_df['Spend/MCap %'] = display_df['spend_to_mcap_ratio'].apply(lambda x: f"{x*100:.3f}%" if pd.notna(x) else "N/A")
        display_df['Quarters'] = display_df['quarters_covered'].replace('', 'N/A')
        display_df['Rows Merged'] = display_df['source_rows']
            
        # Select columns
        display_df = display_df[
            ['company_name', 'ticker', 'Lobbying Spend', 'Market Cap', 'Spend/MCap %', 'Quarters', 'Rows Merged', 'sector']
        ]
        display_df.columns = ['Company', 'Ticker', 'Lobbying Spend', 'Market Cap', 'Spend/MCap %', 'Quarters', 'Rows Merged', 'Sector']
        
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=400
        )

        # Download button — export the raw numeric data (not the formatted display version)
        raw_export = holdings_df.copy()
        raw_export["ticker"] = raw_export["ticker"].fillna("")
        st.download_button(
            label="Download Holdings CSV",
            data=raw_export.to_csv(index=False).encode("utf-8"),
            file_name=f"companies_{selected_year}_{selected_quarter}.csv",
            mime="text/csv",
        )
