"""
Benchmark comparison UI section shared by Performance and Signals tabs.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config

from dashboard.acceleration import get_production_strategy_backtest
from dashboard.benchmark import get_benchmark_comparison
from dashboard.stats import _compute_overfit_diagnostics, _compute_return_risk_stats


def _render_benchmark_section(db_path: str, cache_buster: int, key_prefix: str = "") -> None:
    """
    Renders the Strategy vs. S&P 500 benchmark chart for:
    - All mapped companies (>= $1M Q4)
    - Production model portfolio (Top-N primary/fallback)

    Parameters
    ----------
    db_path      : path to the SQLite database
    cache_buster : st.session_state["cache_buster"] for cache invalidation
    key_prefix   : unique prefix for widget keys (prevents conflicts across tabs)
    """
    st.caption(
        "Equal-weight 1-year forward return from lag-adjusted Q4 signal snapshots. "
        "Year shown is the hold year (signal comes from prior-year filings). "
        "Compares the broad mapped universe (>= $1M Q4) against the production "
        "Top-N model and S&P 500."
    )
    lag_days = int(getattr(config, "LDA_FILING_LAG_DAYS", 20) or 0)
    st.caption(f"Signal lag assumption: {lag_days} day(s) after quarter-end.")
    cost_bps = float(getattr(config, "BACKTEST_REBALANCE_COST_BPS", 0.0) or 0.0)
    if cost_bps > 0:
        st.caption(
            f"Strategy returns are net of turnover-scaled costs ({cost_bps:.1f} bps per 100% turnover)."
        )

    prod_primary_factor = str(
        getattr(config, "PRODUCTION_PRIMARY_FACTOR", "hist_spend_z")
    )
    prod_fallback_factor = str(
        getattr(config, "PRODUCTION_FALLBACK_FACTOR", "accel_score")
    )
    prod_top_n = int(getattr(config, "PRODUCTION_TOP_N", 10))
    prod_min_usable = int(getattr(config, "PRODUCTION_MIN_USABLE_NAMES", 5))
    prod_min_spend = float(getattr(config, "MIN_LOBBYING_SPEND_DEFAULT", 1.0) or 1.0)

    bench_all = get_benchmark_comparison(db_path, refresh_token=cache_buster)
    prod_bt_df, _ = get_production_strategy_backtest(
        db_path,
        top_n=prod_top_n,
        primary_factor=prod_primary_factor,
        fallback_factor=prod_fallback_factor,
        refresh_token=cache_buster,
        min_spend_m=prod_min_spend,
        min_usable_names=prod_min_usable,
    )

    if bench_all.empty and prod_bt_df.empty:
        st.info(
            "No benchmark data yet. Populate stock performance for at least "
            "2 years (Performance tab -> Populate Stock Performance)."
        )
        return

    # Surface any S&P fetch warning from either dataset.
    _spx_err = None
    for _src in [bench_all, prod_bt_df]:
        if (
            not _src.empty
            and "_spx_error" in _src.columns
            and _src["_spx_error"].notna().any()
        ):
            _spx_err = _src["_spx_error"].dropna().iloc[0]
            break
    if _spx_err:
        st.warning(f"S&P 500 data issue: {_spx_err}")

    # Build merged comparison DataFrame (one row per hold year).
    cmp = pd.DataFrame()
    if not bench_all.empty:
        cmp = bench_all[
            [
                "signal_year",
                "year",
                "strategy_return",
                "n_companies",
                "spx_return",
                "turnover_pct",
                "cost_applied_pct",
                "return_coverage_pct",
                "entity_coverage_pct",
                "spend_coverage_pct",
                "mapped_companies",
            ]
        ].copy()
        cmp = cmp.rename(
            columns={
                "strategy_return": "all_ret",
                "n_companies": "all_n",
                "turnover_pct": "all_turnover_pct",
                "cost_applied_pct": "all_cost_pct",
                "return_coverage_pct": "all_return_cov_pct",
                "entity_coverage_pct": "all_entity_cov_pct",
                "spend_coverage_pct": "all_spend_cov_pct",
                "mapped_companies": "all_mapped_n",
            }
        )

    if not prod_bt_df.empty:
        prod_cmp = prod_bt_df[
            [
                "signal_year",
                "year",
                "top_return_1y",
                "n_selected",
                "return_coverage_pct",
                "model_used",
                "fallback_used",
                "spx_return",
            ]
        ].copy()
        prod_cmp = prod_cmp.rename(
            columns={
                "signal_year": "prod_signal_year",
                "top_return_1y": "prod_ret",
                "n_selected": "prod_n",
                "return_coverage_pct": "prod_return_cov_pct",
                "model_used": "prod_model_used",
                "fallback_used": "prod_fallback_used",
                "spx_return": "prod_spx_return",
            }
        )
        if cmp.empty:
            cmp = prod_cmp.copy()
            cmp["signal_year"] = cmp["prod_signal_year"]
            cmp["spx_return"] = cmp["prod_spx_return"]
        else:
            cmp = cmp.merge(prod_cmp, on="year", how="outer")
            if "prod_signal_year" in cmp.columns:
                cmp["signal_year"] = cmp["signal_year"].fillna(cmp["prod_signal_year"])
            if "prod_spx_return" in cmp.columns:
                cmp["spx_return"] = cmp["spx_return"].fillna(cmp["prod_spx_return"])

    for col in [
        "signal_year",
        "all_ret",
        "all_n",
        "all_turnover_pct",
        "all_cost_pct",
        "all_return_cov_pct",
        "all_entity_cov_pct",
        "all_spend_cov_pct",
        "all_mapped_n",
        "prod_ret",
        "prod_n",
        "prod_return_cov_pct",
        "prod_model_used",
        "prod_fallback_used",
    ]:
        if col not in cmp.columns:
            cmp[col] = None

    if "signal_year" in cmp.columns:
        cmp["signal_year"] = cmp["signal_year"].fillna(cmp["year"] - 1)
    else:
        cmp["signal_year"] = cmp["year"] - 1

    cmp["year"] = cmp["year"].astype(int)
    cmp = cmp.sort_values("year").reset_index(drop=True)

    years = cmp["year"].tolist()

    # KPI summary row
    valid = cmp[cmp["spx_return"].notna()].copy()
    spx_avg = valid["spx_return"].mean() if not valid.empty else None

    def _avg_alpha(col):
        if col not in valid.columns or valid[col].isna().all():
            return None
        return round((valid[col] - valid["spx_return"]).mean(), 1)

    def _beat_rate(col):
        if col not in valid.columns or valid[col].isna().all():
            return None
        return round((valid[col] > valid["spx_return"]).mean() * 100, 0)

    kc1, kc2, kc3, kc4, kc5 = st.columns(5)
    kc1.metric("Years Tracked", str(len(cmp)))
    kc2.metric("Avg S&P 500", f"{spx_avg:+.1f}%" if spx_avg is not None else "N/A")

    def _kpi_label(col, label):
        a = _avg_alpha(col)
        b = _beat_rate(col)
        if a is None:
            return "N/A"
        return f"α {a:+.1f}% / {b:.0f}% wins"

    kc3.metric("All companies alpha", _kpi_label("all_ret", "All"))
    kc4.metric(
        f"Production Top {prod_top_n} alpha",
        _kpi_label("prod_ret", f"Top {prod_top_n}"),
    )
    prod_win = _beat_rate("prod_ret")
    kc5.metric(
        f"Production Top {prod_top_n} win rate",
        f"{prod_win:.0f}%" if prod_win is not None else "N/A",
    )
    if "all_turnover_pct" in valid.columns and valid["all_turnover_pct"].notna().any():
        st.caption(
            f"Avg annual turnover (All): {valid['all_turnover_pct'].mean():.1f}% · "
            f"Avg return-data coverage: {valid['all_return_cov_pct'].mean():.1f}%"
        )
    if "prod_return_cov_pct" in valid.columns and valid["prod_return_cov_pct"].notna().any():
        st.caption(
            f"Avg return-data coverage (Production Top {prod_top_n}): "
            f"{valid['prod_return_cov_pct'].mean():.1f}%"
        )

    # Grouped bar chart
    palette = {
        "all": "#60a5fa",   # blue
        "prod": "#34d399",  # green
        "spx": "#f97316",   # orange
    }

    fig = go.Figure()

    def _bar(x, y, name, color):
        valid_mask = [v is not None and not (isinstance(v, float) and pd.isna(v)) for v in y]
        x_v = [x[i] for i in range(len(x)) if valid_mask[i]]
        y_v = [y[i] for i in range(len(y)) if valid_mask[i]]
        if not x_v:
            return
        fig.add_trace(go.Bar(
            x=x_v, y=y_v, name=name,
            marker_color=color,
            text=[f"{v:+.1f}%" for v in y_v],
            textposition="outside",
        ))

    _bar(years, cmp["all_ret"].tolist(), "All (>= $1M Q4)", palette["all"])
    _bar(
        years,
        cmp["prod_ret"].tolist(),
        f"Production Top {prod_top_n}",
        palette["prod"],
    )
    spx_vals = cmp["spx_return"].tolist()
    _bar(years, spx_vals, "S&P 500", palette["spx"])

    fig.add_hline(y=0, line_color="#6b7280", line_width=1)
    fig.update_layout(
        barmode="group",
        xaxis=dict(title="Hold Year", dtick=1, gridcolor="#2d3748", tickvals=years),
        yaxis=dict(title="1-Year Return (%)", gridcolor="#2d3748"),
        plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
        font=dict(color="#ffffff", size=12),
        legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568", borderwidth=1,
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=420,
        margin=dict(l=0, r=40, t=50, b=0),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart")

    # Sample-size notice under the chart
    _n_bt_years = len(years)
    if _n_bt_years < 8:
        st.caption(
            f"**Small-sample warning — {_n_bt_years} annual observation(s).** "
            "Fewer than 8 years of data: alpha estimates, win rates, and Sharpe ratios "
            "are highly sensitive to single-year outcomes. Treat all backtest metrics as "
            "directional indicators rather than statistically reliable figures."
        )

    # Comparison table with alpha rows
    def _fmt(v):
        return f"{v:+.1f}%" if pd.notna(v) and v is not None else "N/A"

    prod_col_label = f"Production Top {prod_top_n} (N)"
    table_rows = []
    for _, r in cmp.iterrows():
        row = {
            "Signal Year": int(r["signal_year"]) if pd.notna(r.get("signal_year")) else "N/A",
            "Hold Year": int(r["year"]),
        }
        row["S&P 500"] = _fmt(r["spx_return"])
        row["All (N)"] = (
            f"{_fmt(r['all_ret'])} ({int(r['all_n']) if pd.notna(r.get('all_n')) else '?'})"
            if pd.notna(r.get("all_ret"))
            else "N/A"
        )
        row[prod_col_label] = (
            f"{_fmt(r['prod_ret'])} ({int(r['prod_n']) if pd.notna(r.get('prod_n')) else '?'})"
            if pd.notna(r.get("prod_ret"))
            else "N/A"
        )
        row["Prod Model"] = (
            str(r["prod_model_used"])
            + (" (fallback)" if bool(r.get("prod_fallback_used")) else "")
            if pd.notna(r.get("prod_model_used"))
            else "N/A"
        )
        table_rows.append(row)

    # Avg alpha summary row
    summary = {"Signal Year": "—", "Hold Year": "Avg Alpha"}
    summary["S&P 500"] = "—"
    for col, label in [("all_ret", "All (N)"), ("prod_ret", prod_col_label)]:
        a = _avg_alpha(col)
        summary[label] = f"{a:+.1f}% vs SPX" if a is not None else "N/A"
    summary["Prod Model"] = "—"
    table_rows.append(summary)

    st.dataframe(
        pd.DataFrame(table_rows),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Execution & Coverage Diagnostics", expanded=False):
        diag_cols = [
            "signal_year",
            "year",
            "all_n",
            "all_mapped_n",
            "all_return_cov_pct",
            "all_entity_cov_pct",
            "all_spend_cov_pct",
            "all_turnover_pct",
            "all_cost_pct",
            "prod_n",
            "prod_model_used",
            "prod_fallback_used",
            "prod_return_cov_pct",
        ]
        diag_cols = [c for c in diag_cols if c in cmp.columns]
        diag = cmp[diag_cols].copy()
        diag = diag.rename(
            columns={
                "signal_year": "Signal Year",
                "year": "Hold Year",
                "all_n": "All N (with returns)",
                "all_mapped_n": "All N (mapped)",
                "all_return_cov_pct": "All Return Coverage",
                "all_entity_cov_pct": "All Entity Coverage",
                "all_spend_cov_pct": "All Spend Coverage",
                "all_turnover_pct": "All Turnover",
                "all_cost_pct": "All Cost Drag",
                "prod_n": "Production N (with returns)",
                "prod_model_used": "Production Model",
                "prod_fallback_used": "Production Fallback Used",
                "prod_return_cov_pct": "Production Return Coverage",
            }
        )
        pct_cols = [
            c
            for c in diag.columns
            if any(k in c for k in ["Coverage", "Turnover", "Cost Drag"])
        ]
        for c in pct_cols:
            diag[c] = diag[c].apply(
                lambda v: f"{v:.2f}%" if pd.notna(v) else "N/A"
            )
        st.dataframe(diag, use_container_width=True, hide_index=True)

    with st.expander("Risk Metrics", expanded=False):
        st.caption(
            "Annual return risk profile using geometric compounding. "
            "Metrics shown: CAGR, volatility, Sharpe, max drawdown, information ratio, beta."
        )
        risk_specs = [
            ("All (≥$1M Q4)", "all_ret"),
            (f"Production Top {prod_top_n}", "prod_ret"),
        ]
        risk_rows = []
        for label, col in risk_specs:
            stats = _compute_return_risk_stats(cmp, col, benchmark_col="spx_return")
            if stats is None:
                continue
            risk_rows.append({
                "Portfolio": label,
                "Years": stats["n_years"],
                "Avg Return": f"{stats['avg_return']:+.1f}%",
                "CAGR": f"{stats['cagr']:+.1f}%",
                "Volatility": f"{stats['vol']:.1f}%",
                "Sharpe": (
                    f"{stats['sharpe']:.2f}" if stats["sharpe"] is not None else "N/A"
                ),
                "Max Drawdown": f"{stats['max_drawdown']:.1f}%",
                "Info Ratio": (
                    f"{stats['info_ratio']:.2f}" if stats["info_ratio"] is not None else "N/A"
                ),
                "Beta vs S&P": (
                    f"{stats['beta']:.2f}" if stats["beta"] is not None else "N/A"
                ),
            })

        if not risk_rows:
            st.info("Need at least 3 annual observations per portfolio to compute risk stats.")
        else:
            st.dataframe(pd.DataFrame(risk_rows), use_container_width=True, hide_index=True)
            # Sample-size warning — Sharpe / drawdown estimates are noisy below ~8 years
            _min_obs = min(r["Years"] for r in risk_rows)
            if _min_obs < 8:
                st.caption(
                    f"**Small sample warning** — based on {_min_obs} annual observation(s). "
                    "Sharpe ratio, max drawdown, and CAGR estimates are unreliable with fewer "
                    "than 8 years of data; the confidence interval on Sharpe alone spans ±1.0 "
                    "at this sample size. Treat all metrics as directional rather than precise."
                )

    st.markdown("---")
    with st.expander("Overfitting Diagnostics", expanded=False):
        st.caption(
            "Chronological train/test split plus bootstrap confidence checks on annual alpha. "
            "Use this to validate whether a strategy remains stable out-of-sample."
        )

        diag_specs = [
            ("All (≥$1M Q4)", "all_ret"),
            (f"Production Top {prod_top_n}", "prod_ret"),
        ]
        diag_rows = []
        raw_pvalues = []
        for label, col in diag_specs:
            diag = _compute_overfit_diagnostics(cmp, col, benchmark_col="spx_return")
            if diag is None:
                continue
            raw_pvalues.append((label, float(diag["p_binom"])))
            diag_rows.append({
                "Portfolio": label,
                "Years": diag["n_years"],
                "Train/Test": diag["split"],
                "Avg Alpha": f"{diag['avg_alpha']:+.1f}%",
                "Train Alpha": f"{diag['train_alpha']:+.1f}%",
                "Test Alpha": f"{diag['test_alpha']:+.1f}%",
                "Alpha Drift": f"{diag['alpha_drift']:+.1f}%",
                "Hit Rate": f"{diag['hit_rate']:.0f}%",
                "Test Hit Rate": f"{diag['test_hit_rate']:.0f}%",
                "95% CI (Mean Alpha)": f"[{diag['ci_low']:+.1f}%, {diag['ci_high']:+.1f}%]",
                "Binom p-value": f"{diag['p_binom']:.3f}",
                "Stability": diag["verdict"],
            })

        if not diag_rows:
            st.info("Need at least 4 annual observations per portfolio to run diagnostics.")
        else:
            # Multiple-testing correction across the tested portfolio variants.
            n_tests = max(1, len(raw_pvalues))
            adj_map = {
                label: min(1.0, p * n_tests) for label, p in raw_pvalues
            }
            for row in diag_rows:
                p_adj = adj_map.get(row["Portfolio"])
                row["Adj p-value"] = f"{p_adj:.3f}" if p_adj is not None else "N/A"
                row["Sig @10%"] = (
                    "Yes" if p_adj is not None and p_adj < 0.10 else "No"
                )

            diag_df = pd.DataFrame(diag_rows)
            st.dataframe(diag_df, use_container_width=True, hide_index=True)

            fragile = diag_df[diag_df["Stability"] == "Fragile"]["Portfolio"].tolist()
            if fragile:
                st.warning(
                    "Potential overfitting risk: "
                    + ", ".join(fragile)
                    + " show weaker out-of-sample alpha than in-sample."
                )
            if len(diag_df) > 0 and (diag_df["Sig @10%"] == "No").all():
                st.caption(
                    "None of the tested portfolio variants pass a 10% significance threshold "
                    "after multiple-testing adjustment."
                )
