"""
Company Research tab.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.company import (
    get_company_annual_spend,
    get_company_entity_names,
    get_company_lobbyist_firms,
    get_company_sector_peers,
    get_company_stock_snapshots,
    search_companies,
)
from dashboard.formatting import format_currency
from dashboard.queries import get_issue_breakdown_for_ticker


def render(fetcher):
    """Render the Company Research tab."""
    st.markdown("### Company Research")
    st.caption(
        "Search by ticker symbol or company name to see a full lobbying and "
        "stock performance history for that entity."
    )

    # ── Search bar ────────────────────────────────────────────────────────
    cr_query = st.text_input(
        "Search ticker or company name",
        placeholder="e.g. MSFT, Microsoft, Pfizer, AMZN",
        key="cr_search_query",
    ).strip()

    if not cr_query:
        st.info(
            "Enter a ticker symbol or company name above to begin. "
            "Examples: MSFT, META, Pfizer, Lockheed, AMZN"
        )
    else:
        cr_results = search_companies(
            fetcher.db_path, cr_query,
            refresh_token=st.session_state["cache_buster"],
        )

        if cr_results.empty:
            st.warning(
                f"No companies found matching '{cr_query}'. "
                "Try the ticker symbol (e.g. MSFT) or a shorter name."
            )
        else:
            # ── Company selector ──────────────────────────────────────────
            if len(cr_results) == 1:
                selected_cr = cr_results.iloc[0]
            else:
                cr_options = {
                    f"{r['ticker']}  —  {r.get('display_name', r['ticker'])}  "
                    f"(${r['total_spend']/1e6:.1f}M total, "
                    f"{r['earliest_year']}–{r['latest_year']})": i
                    for i, r in cr_results.iterrows()
                }
                chosen_label = st.selectbox(
                    f"Found {len(cr_results)} match(es) — select one:",
                    options=list(cr_options.keys()),
                    key="cr_selector",
                )
                selected_cr = cr_results.loc[cr_options[chosen_label]]

            cr_ticker  = selected_cr["ticker"]
            cr_sector  = selected_cr.get("sector", "Unknown")
            cr_name    = selected_cr.get("display_name", cr_ticker)
            cr_yrs_act = int(selected_cr["years_active"])
            cr_earliest = int(selected_cr["earliest_year"])
            cr_latest   = int(selected_cr["latest_year"])

            # ── Load all data ─────────────────────────────────────────────
            with st.spinner(f"Loading data for {cr_ticker}..."):
                cr_annual  = get_company_annual_spend(
                    fetcher.db_path, cr_ticker,
                    refresh_token=st.session_state["cache_buster"],
                )
                cr_stock   = get_company_stock_snapshots(
                    fetcher.db_path, cr_ticker,
                    refresh_token=st.session_state["cache_buster"],
                )
                cr_entities = get_company_entity_names(
                    fetcher.db_path, cr_ticker,
                    refresh_token=st.session_state["cache_buster"],
                )
                entity_key = "|".join(sorted(cr_entities))
                cr_firms   = get_company_lobbyist_firms(
                    fetcher.db_path, entity_key,
                    refresh_token=st.session_state["cache_buster"],
                )
                cr_peers   = get_company_sector_peers(
                    fetcher.db_path, cr_sector, cr_ticker,
                    refresh_token=st.session_state["cache_buster"],
                ) if cr_sector and cr_sector != "Unknown" else pd.DataFrame()

            # ── Company header ────────────────────────────────────────────
            st.markdown(f"---")
            st.markdown(f"### {cr_name}  (`{cr_ticker}`)")
            st.caption(
                f"Sector: {cr_sector}  |  "
                f"Data: {cr_earliest}–{cr_latest}  |  "
                f"{cr_yrs_act} year(s) in database"
            )

            if cr_annual.empty:
                st.warning("No aggregated lobbying data found for this ticker.")
            else:
                latest_yr_row = cr_annual.iloc[-1]
                prev_yr_row   = cr_annual.iloc[-2] if len(cr_annual) > 1 else None

                latest_spend  = latest_yr_row["total"]
                latest_yoy    = latest_yr_row["yoy_pct"]
                latest_smcap  = latest_yr_row.get("spend_to_mcap_pct")
                latest_yr_lbl = int(latest_yr_row["year"])

                latest_ret = None
                if not cr_stock.empty:
                    stock_row = cr_stock[cr_stock["year"] == latest_yr_lbl - 1]
                    if not stock_row.empty:
                        latest_ret = stock_row.iloc[0]["return_1y"]

                # KPIs
                k1, k2, k3, k4, k5 = st.columns(5)
                with k1:
                    st.metric(
                        f"Total Spend ({latest_yr_lbl})",
                        format_currency(latest_spend),
                    )
                with k2:
                    yoy_str = (
                        f"{latest_yoy:+.1f}%" if pd.notna(latest_yoy) else "N/A"
                    )
                    st.metric("YoY% Change", yoy_str)
                with k3:
                    smcap_str = (
                        f"{latest_smcap:.3f}%"
                        if latest_smcap is not None and pd.notna(latest_smcap)
                        else "N/A"
                    )
                    st.metric("Spend / Market Cap", smcap_str)
                with k4:
                    ret_str = (
                        f"{latest_ret:+.1f}%"
                        if latest_ret is not None and pd.notna(latest_ret)
                        else "N/A"
                    )
                    st.metric(f"1Y Stock Return ({latest_yr_lbl - 1})", ret_str)
                with k5:
                    n_firms_latest = (
                        int(cr_firms[cr_firms["year"] == latest_yr_lbl]["n_firms"].iloc[0])
                        if not cr_firms.empty and latest_yr_lbl in cr_firms["year"].values
                        else None
                    )
                    st.metric(
                        "Lobbying Firms Hired",
                        str(n_firms_latest) if n_firms_latest is not None else "N/A",
                    )

                st.markdown("---")

                # ── Chart 1: Stacked quarterly spend + stock price overlay ─
                st.markdown("#### Lobbying Spend History")
                cr_years = cr_annual["year"].tolist()
                q_colors = {"Q1": "#3b82f6", "Q2": "#60a5fa",
                            "Q3": "#93c5fd", "Q4": "#bfdbfe"}

                fig_spend = go.Figure()
                for q in ["Q1", "Q2", "Q3", "Q4"]:
                    if q in cr_annual.columns:
                        fig_spend.add_trace(go.Bar(
                            x=cr_years,
                            y=cr_annual[q].tolist(),
                            name=q,
                            marker_color=q_colors[q],
                            hovertemplate=(
                                f"{q}: $%{{y:,.0f}}<extra></extra>"
                            ),
                        ))

                # Stock price line on secondary y-axis
                if not cr_stock.empty:
                    merged_stock = cr_annual[["year"]].merge(
                        cr_stock[["year", "close_price"]], on="year", how="left"
                    )
                    fig_spend.add_trace(go.Scatter(
                        x=merged_stock["year"].tolist(),
                        y=merged_stock["close_price"].tolist(),
                        name="Stock Price (Dec 31, $)",
                        mode="lines+markers",
                        line=dict(color="#f97316", width=2.5),
                        marker=dict(size=6),
                        yaxis="y2",
                        hovertemplate="Stock: $%{y:,.2f}<extra></extra>",
                    ))

                fig_spend.update_layout(
                    barmode="stack",
                    xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748",
                               tickvals=cr_years),
                    yaxis=dict(title="Lobbying Spend ($)", gridcolor="#2d3748",
                               tickformat="$,.0f"),
                    yaxis2=dict(title="Stock Price ($)", overlaying="y",
                                side="right", showgrid=False, tickformat="$,.0f"),
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                borderwidth=1, orientation="h",
                                yanchor="bottom", y=1.02, xanchor="right", x=1),
                    height=400,
                    margin=dict(l=0, r=60, t=50, b=0),
                )
                st.plotly_chart(fig_spend, use_container_width=True,
                                key="cr_spend_chart")

                # ── Chart 2: YoY% Lobbying vs 1Y Stock Return ─────────────
                st.markdown("#### Lobbying Growth vs Stock Return")
                st.caption(
                    "Bars show year-over-year lobbying spend change. "
                    "Orange line shows 1-year forward stock return for the same year. "
                    "A lead effect would appear as lobbying ramps before the stock return improves."
                )

                if not cr_stock.empty:
                    yoy_stock_df = cr_annual[["year", "yoy_pct"]].merge(
                        cr_stock[["year", "return_1y"]], on="year", how="left"
                    )
                else:
                    yoy_stock_df = cr_annual[["year", "yoy_pct"]].copy()
                    yoy_stock_df["return_1y"] = None

                fig_yoy = go.Figure()
                bar_colors_yoy = [
                    "#22c55e" if (pd.notna(v) and v >= 0) else "#ef4444"
                    for v in yoy_stock_df["yoy_pct"]
                ]
                fig_yoy.add_trace(go.Bar(
                    x=yoy_stock_df["year"].tolist(),
                    y=yoy_stock_df["yoy_pct"].tolist(),
                    name="Lobbying YoY%",
                    marker_color=bar_colors_yoy,
                    text=[
                        f"{v:+.1f}%" if pd.notna(v) else ""
                        for v in yoy_stock_df["yoy_pct"]
                    ],
                    textposition="outside",
                    hovertemplate="Lobbying YoY: %{y:+.1f}%<extra></extra>",
                ))

                valid_ret = yoy_stock_df["return_1y"].notna()
                if valid_ret.any():
                    fig_yoy.add_trace(go.Scatter(
                        x=yoy_stock_df["year"].tolist(),
                        y=yoy_stock_df["return_1y"].tolist(),
                        name="1Y Stock Return",
                        mode="lines+markers",
                        line=dict(color="#f97316", width=2.5, dash="dot"),
                        marker=dict(size=7, symbol="diamond"),
                        hovertemplate="1Y Return: %{y:+.1f}%<extra></extra>",
                    ))

                fig_yoy.add_hline(y=0, line_color="#6b7280", line_width=1)
                fig_yoy.update_layout(
                    xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748",
                               tickvals=cr_years),
                    yaxis=dict(title="% Change", gridcolor="#2d3748"),
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                borderwidth=1, orientation="h",
                                yanchor="bottom", y=1.02, xanchor="right", x=1),
                    height=360,
                    margin=dict(l=0, r=40, t=50, b=0),
                )
                st.plotly_chart(fig_yoy, use_container_width=True,
                                key="cr_yoy_chart")

                # ── Chart 3: Spend / Market Cap ratio ─────────────────────
                if cr_annual["spend_to_mcap_pct"].notna().any():
                    st.markdown("#### Lobbying Intensity (Spend / Market Cap)")
                    st.caption(
                        "Normalises lobbying spend by company size. "
                        "A rising ratio means the company is increasing its "
                        "lobbying commitment relative to its market value."
                    )
                    fig_smcap = go.Figure()
                    fig_smcap.add_trace(go.Scatter(
                        x=cr_years,
                        y=cr_annual["spend_to_mcap_pct"].tolist(),
                        mode="lines+markers",
                        line=dict(color="#a78bfa", width=2.5),
                        marker=dict(size=7),
                        fill="tozeroy",
                        fillcolor="rgba(167, 139, 250, 0.15)",
                        hovertemplate="Spend/MCap: %{y:.4f}%<extra></extra>",
                    ))
                    fig_smcap.update_layout(
                        xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748",
                                   tickvals=cr_years),
                        yaxis=dict(title="Spend / Market Cap (%)",
                                   gridcolor="#2d3748"),
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        height=280,
                        margin=dict(l=0, r=40, t=30, b=0),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_smcap, use_container_width=True,
                                    key="cr_smcap_chart")

                # ── Chart 4: Lobbying firms hired ─────────────────────────
                if not cr_firms.empty:
                    st.markdown("#### External Lobbying Firms Hired")
                    st.caption(
                        "Number of unique lobbying firms (registrants) retained "
                        "per year. A sudden increase signals a major new "
                        "regulatory or legislative push."
                    )
                    fig_firms = go.Figure()
                    fig_firms.add_trace(go.Bar(
                        x=cr_firms["year"].tolist(),
                        y=cr_firms["n_firms"].tolist(),
                        name="Lobbying Firms",
                        marker_color="#34d399",
                        text=cr_firms["n_firms"].tolist(),
                        textposition="outside",
                        hovertemplate=(
                            "Year %{x}<br>"
                            "Firms: %{y}<br>"
                            "Paid: $%{customdata:,.0f}<extra></extra>"
                        ),
                        customdata=cr_firms["total_paid"].tolist(),
                    ))
                    fig_firms.update_layout(
                        xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748"),
                        yaxis=dict(title="# Lobbying Firms", gridcolor="#2d3748"),
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        height=280,
                        margin=dict(l=0, r=40, t=30, b=0),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_firms, use_container_width=True,
                                    key="cr_firms_chart")

                # ── Chart 5: Sector peer comparison ───────────────────────
                if not cr_peers.empty:
                    st.markdown(f"#### Sector Peers — Lobbying Spend ({cr_sector})")
                    st.caption(
                        "Top 8 spenders in the same sector. "
                        "Shows whether this company is gaining or losing "
                        "lobbying share within its industry."
                    )
                    peer_tickers = cr_peers["ticker"].unique().tolist()
                    # Give the highlighted company a distinct colour
                    peer_palette = [
                        "#f97316" if t == cr_ticker else "#3b82f6"
                        for t in peer_tickers
                    ]

                    fig_peers = go.Figure()
                    for ticker_p, color_p in zip(peer_tickers, peer_palette):
                        td = cr_peers[cr_peers["ticker"] == ticker_p]
                        fig_peers.add_trace(go.Scatter(
                            x=td["year"].tolist(),
                            y=td["total"].tolist(),
                            name=ticker_p,
                            mode="lines+markers",
                            line=dict(
                                color=color_p,
                                width=3.0 if ticker_p == cr_ticker else 1.5,
                            ),
                            marker=dict(
                                size=8 if ticker_p == cr_ticker else 5,
                            ),
                            hovertemplate=(
                                f"{ticker_p}: $%{{y:,.0f}}<extra></extra>"
                            ),
                        ))

                    fig_peers.update_layout(
                        xaxis=dict(title="Year", dtick=1, gridcolor="#2d3748"),
                        yaxis=dict(title="Annual Lobbying Spend ($)",
                                   gridcolor="#2d3748", tickformat="$,.0f"),
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568",
                                    borderwidth=1, orientation="h",
                                    yanchor="bottom", y=1.02,
                                    xanchor="right", x=1),
                        height=360,
                        margin=dict(l=0, r=40, t=50, b=0),
                    )
                    st.plotly_chart(fig_peers, use_container_width=True,
                                    key="cr_peers_chart")

                # ── Data table: annual summary ─────────────────────────────
                st.markdown("#### Annual Summary Table")
                summary_tbl = cr_annual.copy()
                if not cr_stock.empty:
                    summary_tbl = summary_tbl.merge(
                        cr_stock[["year", "close_price", "return_1y"]],
                        on="year", how="left",
                    )
                if not cr_firms.empty:
                    summary_tbl = summary_tbl.merge(
                        cr_firms[["year", "n_firms"]],
                        on="year", how="left",
                    )

                def _fmt_spend(v):
                    return format_currency(v) if pd.notna(v) else "—"

                def _fmt_pct(v):
                    return f"{v:+.1f}%" if pd.notna(v) else "—"

                def _fmt_price(v):
                    return f"${v:,.2f}" if pd.notna(v) else "—"

                disp_tbl = pd.DataFrame({
                    "Year": summary_tbl["year"].astype(int),
                    "Total Spend": summary_tbl["total"].apply(_fmt_spend),
                    "Q1": summary_tbl["Q1"].apply(_fmt_spend),
                    "Q2": summary_tbl["Q2"].apply(_fmt_spend),
                    "Q3": summary_tbl["Q3"].apply(_fmt_spend),
                    "Q4": summary_tbl["Q4"].apply(_fmt_spend),
                    "YoY%": summary_tbl["yoy_pct"].apply(_fmt_pct),
                    "Spend/MCap": summary_tbl["spend_to_mcap_pct"].apply(
                        lambda v: f"{v:.4f}%" if pd.notna(v) else "—"
                    ),
                })
                if "close_price" in summary_tbl.columns:
                    disp_tbl["Stock (Dec 31)"] = summary_tbl["close_price"].apply(
                        _fmt_price
                    )
                if "return_1y" in summary_tbl.columns:
                    disp_tbl["1Y Return"] = summary_tbl["return_1y"].apply(
                        _fmt_pct
                    )
                if "n_firms" in summary_tbl.columns:
                    disp_tbl["Lobbying Firms"] = summary_tbl["n_firms"].apply(
                        lambda v: str(int(v)) if pd.notna(v) else "—"
                    )

                st.dataframe(
                    disp_tbl.sort_values("Year", ascending=False),
                    use_container_width=True, hide_index=True,
                )

                # Download
                st.download_button(
                    "Download Company Research CSV",
                    data=summary_tbl.to_csv(index=False).encode("utf-8"),
                    file_name=f"{cr_ticker}_lobbying_research.csv",
                    mime="text/csv",
                    key="cr_download",
                )

                # ── Issue Code Breakdown ───────────────────────────────────
                st.markdown("---")
                st.markdown("#### Lobbying Issue Breakdown")
                st.caption(
                    "What policy areas is this company lobbying on? Issue codes follow "
                    "the Senate LDA 76-code taxonomy. Research shows issue-specific "
                    "lobbying (TAX, DEF, HCR) is the strongest predictor of alpha. "
                    "Populated automatically on each data refresh."
                )
                cr_issues = get_issue_breakdown_for_ticker(
                    fetcher.db_path,
                    cr_ticker,
                    refresh_token=st.session_state["cache_buster"],
                )
                if cr_issues.empty:
                    st.info(
                        "No issue-code data yet — trigger a data refresh from the sidebar "
                        "to populate the filing_issues table from the Senate LDA API."
                    )
                else:
                    _ISSUE_LABELS = {
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
                    cr_issues["issue_label"] = (
                        cr_issues["general_issue_code"]
                        .map(_ISSUE_LABELS)
                        .fillna(cr_issues["general_issue_code"])
                    )
                    pivot_iss = cr_issues.pivot_table(
                        index="year", columns="issue_label",
                        values="n_activities", aggfunc="sum", fill_value=0,
                    ).reset_index().sort_values("year")

                    top8 = (
                        cr_issues.groupby("issue_label")["n_activities"]
                        .sum().nlargest(8).index.tolist()
                    )
                    top8_cols = [c for c in top8 if c in pivot_iss.columns]

                    _ISS_COLORS = [
                        "#3b82f6", "#22c55e", "#f59e0b", "#ef4444",
                        "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16",
                    ]
                    fig_iss = go.Figure()
                    for ci, issue in enumerate(top8_cols):
                        fig_iss.add_trace(go.Bar(
                            x=pivot_iss["year"].tolist(),
                            y=pivot_iss[issue].tolist(),
                            name=issue,
                            marker_color=_ISS_COLORS[ci % len(_ISS_COLORS)],
                            hovertemplate=f"{issue}: %{{y}}<extra></extra>",
                        ))
                    fig_iss.update_layout(
                        barmode="stack",
                        xaxis=dict(title="Year", dtick=1,
                                   tickvals=pivot_iss["year"].tolist(),
                                   gridcolor="#2d3748"),
                        yaxis=dict(title="Issue Activities",
                                   gridcolor="#2d3748"),
                        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                        font=dict(color="#ffffff", size=12),
                        legend=dict(orientation="h", yanchor="bottom",
                                    y=1.02, xanchor="right", x=1),
                        height=320,
                        margin=dict(l=0, r=0, t=50, b=0),
                    )
                    st.plotly_chart(fig_iss, use_container_width=True,
                                    key="cr_issue_chart")

                    # Latest-year breakdown table
                    latest_iss_yr = int(cr_issues["year"].max())
                    top_issues_tbl = (
                        cr_issues[cr_issues["year"] == latest_iss_yr]
                        .nlargest(10, "n_activities")
                        [["issue_label", "n_activities"]]
                        .rename(columns={
                            "issue_label":  "Issue Area",
                            "n_activities": f"Activities ({latest_iss_yr})",
                        })
                        .reset_index(drop=True)
                    )
                    st.dataframe(top_issues_tbl, use_container_width=True,
                                 hide_index=True, key="cr_issue_table")
