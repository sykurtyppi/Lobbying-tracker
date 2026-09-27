"""
Performance tab.
"""

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config

from dashboard.acceleration import (
    get_acceleration_event_backtest,
    get_acceleration_features,
    get_acceleration_oos_split_backtest,
    get_acceleration_rolling_windows,
    get_acceleration_simplicity_check,
    get_production_go_nogo_metrics,
    get_production_strategy_backtest,
    get_production_strategy_holdings,
    get_sector_neutral_acceleration_backtest,
)
from dashboard.benchmark import _q4_signal_window, get_signal_returns
from dashboard.formatting import format_currency
from dashboard.leaderboard import get_yearly_summary
from dashboard.queries import get_available_years
from dashboard.regime import get_regime_conditioned_acceleration_performance
from dashboard.sections.benchmark_section import _render_benchmark_section


def render(fetcher, selected_year, min_spend):
    """Render the Performance tab."""
    st.markdown("### Strategy Analytics")
    st.info(
        "**Start here:** Use **Production Picks** as the primary decision list. "
        "All strategy performance panels below are aligned to that production model."
    )

    # ── Model Assumptions & Methodology ───────────────────────────────────
    with st.expander("📐 Model Assumptions & Methodology", expanded=False):
        st.markdown(
            """
**What this model does**

Lobbying filings reported to the Senate LDA are used as a forward-looking signal:
companies that *accelerate* lobbying spend are hypothesised to be anticipating
regulatory tailwinds, contract awards, or policy outcomes not yet reflected in prices.
The portfolio is ranked, formed at year-end, and held for the following calendar year.

---

**Key assumptions — understand before interpreting results**

| Assumption | Detail |
|---|---|
| **Signal lag** | Senate LDA filings are public ~45 days after each quarter-end. The model uses a 20-day lag from Q4 year-end (≈ Jan 20) as the signal anchor date. |
| **Hold period** | 12 months (full calendar year following signal year). |
| **Transaction costs** | Equal-weight turnover × configurable cost bps (default: set in Settings). Applied symmetrically on buys and sells. |
| **Universe** | Ticker-matched public companies with ≥ $1M annual lobbying spend. Private companies, NGOs, trade associations, and governments are excluded. |
| **Returns** | Equal-weight average of constituent 1-year forward returns from `stock_performance` table (sourced via Yahoo Finance). |
| **Regime filter** | VIX, SPY/200-DMA, LQD/HYG credit ratio, and 10Y-3M yield spread — *not* a formally calibrated model. Acts as a macro stress overlay: ≥ 2 flags → 50% portfolio sizing for that year. |
| **Backtest depth** | 2019–present. Limited history means statistical significance is low (n ≈ 5-6 independent years). Treat results as directional evidence, not proof of edge. |
| **Survivorship** | No survivorship bias correction. Delistings and M&A exits during the hold year are not explicitly handled; Yahoo Finance may return partial returns or NaN. |
| **Sector classification** | Pulled from Yahoo Finance `info.sector` at time of data fetch; point-in-time sector may differ. |

---

**What the conviction score measures (NOT what the backtest uses)**

The conviction score (0–100) ranks companies for monitoring purposes. The *production backtest*
uses the `hist_spend_z` composite, not conviction scores directly. Conviction scores are
a multi-factor screening tool, not a backtest-validated signal on their own.

---

**Limitations you should know**

- 5–6 years of data is below the threshold for robust statistical inference.
- The regime filter improves theoretical soundness but adds one more unvalidated assumption.
- The signal works better in risk-on regimes; performance during 2022's rate-shock year should be interpreted with that in mind.
- OpenSecrets PAC data adds a dimension but is tied to election-cycle reporting, not calendar years.
                """
        )

    # ── Stock performance coverage banner ─────────────────────────────────
    import sqlite3 as _sp_sq3
    _sp_conn = _sp_sq3.connect(fetcher.db_path)
    try:
        _sp_years = pd.read_sql_query(
            """SELECT CAST(strftime('%Y', date) AS INTEGER) AS yr,
                          COUNT(DISTINCT ticker) as n
                   FROM stock_performance GROUP BY yr ORDER BY yr""",
            _sp_conn,
        )
    except Exception:
        _sp_years = pd.DataFrame()
    finally:
        _sp_conn.close()

    _all_lobby_years = sorted([
        y for y in get_available_years(
            fetcher.db_path, st.session_state["cache_buster"]
        ) if y <= (datetime.now().year - 1)
    ])
    _sp_covered = _sp_years["yr"].tolist() if not _sp_years.empty else []
    _sp_missing = [y for y in _all_lobby_years if y not in _sp_covered]

    if _sp_missing:
        st.info(
            f"Stock price data is not yet populated for "
            f"**{', '.join(str(y) for y in _sp_missing)}**. "
            "Return metrics for those years will show as N/A until "
            "**Populate Stock Performance** is run in Settings "
            f"(set lookback to {len(_all_lobby_years)} years). "
            "Lobbying data for all years is fully available."
        )
    else:
        st.caption(
            f"Stock performance populated for all years: "
            f"{', '.join(str(y) for y in _sp_covered)}."
        )

    # ── Section 1: Production Picks (Next Hold Year) ─────────────────────
    prod_primary_factor_cfg = str(
        getattr(config, "PRODUCTION_PRIMARY_FACTOR", "hist_spend_z")
    )
    prod_fallback_factor_cfg = str(
        getattr(config, "PRODUCTION_FALLBACK_FACTOR", "accel_score")
    )
    prod_top_n_cfg = int(getattr(config, "PRODUCTION_TOP_N", 10))
    prod_min_usable_cfg = int(getattr(config, "PRODUCTION_MIN_USABLE_NAMES", 5))
    prod_signal_year = int(selected_year)
    prod_hold_year_next = int(selected_year) + 1

    st.markdown(
        f"#### Production Picks — Signal {prod_signal_year} -> Hold {prod_hold_year_next}"
    )
    st.caption(
        "Primary model ranks public companies by **hist_spend_z** "
        "(lobbying spend spike vs own history). "
        "Fallback model uses **composite acceleration score** if primary coverage is thin."
    )
    st.caption(
        "Interpretation: names below are the current model watchlist generated from "
        f"{prod_signal_year} lobbying filings for trading into {prod_hold_year_next}."
    )

    prod_next_df, prod_next_meta = get_production_strategy_holdings(
        fetcher.db_path,
        hold_year=prod_hold_year_next,
        top_n=prod_top_n_cfg,
        primary_factor=prod_primary_factor_cfg,
        fallback_factor=prod_fallback_factor_cfg,
        refresh_token=st.session_state["cache_buster"],
        min_spend_m=min_spend,
        min_usable_names=prod_min_usable_cfg,
    )

    if prod_next_df.empty:
        st.info(
            f"No production picks available for signal year {prod_signal_year} "
            f"(hold year {prod_hold_year_next})."
        )
    else:
        pp1, pp2, pp3, pp4 = st.columns(4)
        pp1.metric("Signal Year", f"{prod_signal_year}")
        pp2.metric("Target Hold Year", f"{prod_hold_year_next}")
        pp3.metric("Model Used", str(prod_next_meta.get("model_used", "N/A")))
        pp4.metric("Names", f"{int(prod_next_meta.get('selected_n', len(prod_next_df)))}")

        if prod_next_meta.get("reason"):
            st.caption(str(prod_next_meta.get("reason")))

        prod_list = prod_next_df.copy().reset_index(drop=True)
        prod_list.insert(0, "Rank", prod_list.index + 1)
        prod_list["annual_spend"] = prod_list["annual_spend"].apply(format_currency)
        for c in ["hist_spend_z", "accel_score"]:
            if c in prod_list.columns:
                prod_list[c] = prod_list[c].apply(
                    lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                )
        prod_list = prod_list.rename(
            columns={
                "ticker": "Ticker",
                "company_name": "Company",
                "sector": "Sector",
                "annual_spend": "Annual Spend",
                "hist_spend_z": "Hist Z",
                "accel_score": "Composite",
            }
        )
        st.dataframe(
            prod_list[
                [
                    c
                    for c in [
                        "Rank",
                        "Ticker",
                        "Company",
                        "Sector",
                        "Annual Spend",
                        "Hist Z",
                        "Composite",
                    ]
                    if c in prod_list.columns
                ]
            ],
            use_container_width=True,
            hide_index=True,
            height=360,
        )

    st.markdown("---")

    # ── Section 2: Signal Returns ──────────────────────────────────────────
    st.markdown("#### Signal Validation — Forward Returns")

    # ── How returns are measured (collapsible explainer) ──────────────────
    _hold_year = int(selected_year)
    _signal_year = _hold_year - 1
    _today = datetime.now().replace(tzinfo=None)
    _lag_days = int(getattr(config, "LDA_FILING_LAG_DAYS", 20) or 0)
    _ref_date = pd.to_datetime(
        _q4_signal_window(_signal_year, window_days=46)[0]
    ).to_pydatetime()
    _period_meta = {
        "return_1m": ("1-Month",  _ref_date + timedelta(days=30)),
        "return_3m": ("3-Month",  _ref_date + timedelta(days=91)),
        "return_6m": ("6-Month",  _ref_date + timedelta(days=182)),
        "return_1y": ("1-Year",   _ref_date + timedelta(days=365)),
    }

    with st.expander("How are forward returns calculated?", expanded=False):
        st.markdown(
            f"Selected year is treated as the **hold year** (`{_hold_year}`). "
            f"Signals come from prior-year Q4 filings (`{_signal_year}`), anchored to "
            f"**{_ref_date.strftime('%b %d, %Y')}** "
            f"(`{_signal_year}` Q4 + {_lag_days} day lag). "
            f"Each window then looks forward:\n\n"
            + "\n".join(
                f"- **{lbl}**: measures stock price change from "
                f"{_ref_date.strftime('%b %d, %Y')} → "
                f"{tgt.strftime('%b %d, %Y')} "
                f"({'past' if tgt <= _today else 'future'})"
                for _, (lbl, tgt) in _period_meta.items()
            )
        )

    signal_df = get_signal_returns(
        fetcher.db_path,
        _signal_year,
        refresh_token=st.session_state["cache_buster"],
    )

    if signal_df.empty:
        st.info(
            f"No return data found for hold year `{_hold_year}` "
            f"(signal year `{_signal_year}`). "
            "Run **Populate Stock Performance** in the sidebar "
            "(Advanced: Enrich Existing Data) to unlock this section."
        )
    else:
        # ── Per-period staleness analysis ─────────────────────────────────
        # A period is "stale" if its target date is in the past but the DB
        # has fewer than 20% of companies with a return value — meaning
        # populate_stock_performance was run before that date and never re-run.
        _n_total = len(signal_df)
        _period_status: dict[str, tuple] = {}
        _stale_labels: list[str] = []

        for _col, (_lbl, _tgt) in _period_meta.items():
            _n_avail = int(signal_df[_col].notna().sum())
            if _tgt > _today:
                _period_status[_col] = ("future", _tgt.strftime("%b %Y"),
                                        _n_avail, _n_total)
            elif _n_avail < max(1, _n_total * 0.2):
                _period_status[_col] = ("stale", _tgt.strftime("%b %Y"),
                                        _n_avail, _n_total)
                _stale_labels.append(_lbl)
            else:
                _period_status[_col] = ("ok", None, _n_avail, _n_total)

        _future_periods = [
            (_lbl, _tgt.strftime("%b %d, %Y"), _period_status[_col][2])
            for _col, (_lbl, _tgt) in _period_meta.items()
            if _period_status[_col][0] == "future"
        ]

        # ── Staleness banner + inline Refresh button ──────────────────────
        if _stale_labels:
            _ban_c1, _ban_c2 = st.columns([4, 1])
            with _ban_c1:
                st.warning(
                    f"**Stock data stale for hold year {_hold_year}.** "
                    f"The **{', '.join(_stale_labels)}** window"
                    f"{'s have' if len(_stale_labels) > 1 else ' has'} already passed "
                    f"but return data was never computed — `populate_stock_performance` "
                    f"was last run before those target dates. "
                    f"Click **Refresh** to fill in the missing returns now."
                )
            with _ban_c2:
                if st.button(
                    "Refresh Now",
                    key="signal_refresh_sp",
                    use_container_width=True,
                    type="primary",
                ):
                    with st.spinner("Fetching price history from Yahoo Finance…"):
                        try:
                            from build_company_lobbying import populate_stock_performance
                            _r = populate_stock_performance(
                                db_path=fetcher.db_path, lookback_years=7
                            )
                            st.success(
                                f"Done: {_r['tickers_processed']} tickers, "
                                f"{_r['rows_inserted']} rows updated."
                            )
                            st.session_state["cache_buster"] += 1
                            st.rerun()
                        except Exception as _e:
                            st.error(f"Refresh failed: {_e}")

        if _future_periods:
            _pending_txt = ", ".join(
                f"{_lbl} (target: {_dt}; available now: {_n_avail}/{_n_total})"
                for _lbl, _dt, _n_avail in _future_periods
            )
            st.info(
                f"Forward windows still pending for hold year `{_hold_year}` "
                f"(signal year `{_signal_year}`): {_pending_txt}. "
                "This is expected for the latest hold year. Re-run "
                "**Populate Stock Performance** after each target date passes."
            )

        # ── KPI metrics — smart display per period ────────────────────────
        sr1, sr2, sr3, sr4 = st.columns(4)
        for _display_col, _col, _base_label in [
            (sr1, "return_1m", "1-Month"),
            (sr2, "return_3m", "3-Month"),
            (sr3, "return_6m", "6-Month"),
            (sr4, "return_1y", "1-Year"),
        ]:
            _val = signal_df[_col].dropna().mean()
            _status, _tgt_str, _n_avail, _ = _period_status[_col]

            if pd.notna(_val):
                _display_col.metric(
                    f"Avg {_base_label}",
                    f"{_val:+.1f}%",
                    f"{_n_avail}/{_n_total} companies",
                )
            elif _status == "future":
                _display_col.metric(
                    f"Avg {_base_label}",
                    f"~{_tgt_str}",
                    "Not reached yet",
                    delta_color="off",
                )
            elif _status == "stale":
                _display_col.metric(
                    f"Avg {_base_label}",
                    "Refresh ↑",
                    f"Target: {_tgt_str}",
                    delta_color="off",
                )
            else:
                _display_col.metric(f"Avg {_base_label}", "N/A", delta_color="off")

        st.markdown("---")

        # ── Distribution histogram — use best available period ─────────────
        # Pick longest period that actually has data, falling back gracefully.
        _best_period_col = None
        _best_period_lbl = None
        for _col, (_lbl, _) in reversed(list(_period_meta.items())):
            if signal_df[_col].notna().sum() > 2:
                _best_period_col = _col
                _best_period_lbl = _lbl
                break

        win_col1, win_col2 = st.columns(2)
        with win_col1:
            if _best_period_col:
                st.markdown(f"**{_best_period_lbl} Return Distribution**")
                rets = signal_df[_best_period_col].dropna()
                fig_hist = go.Figure()
                fig_hist.add_trace(go.Histogram(
                    x=rets, nbinsx=20,
                    marker_color="#60a5fa", opacity=0.85,
                ))
                fig_hist.add_vline(
                    x=0, line_color="#ef4444", line_width=2, line_dash="dash"
                )
                fig_hist.update_layout(
                    xaxis_title=f"{_best_period_lbl} Return (%)",
                    yaxis_title="Count",
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=11),
                    height=300, margin=dict(l=0, r=0, t=10, b=0),
                )
                st.plotly_chart(fig_hist, use_container_width=True)
            else:
                st.info("No return data available for histogram yet.")

        with win_col2:
            if _best_period_col:
                rets = signal_df[_best_period_col].dropna()
                st.markdown(f"**{_best_period_lbl} Return Stats**")
                win_rate = (rets > 0).mean() * 100
                st.metric("Win Rate (>0%)", f"{win_rate:.1f}%")
                st.metric(f"Median {_best_period_lbl}", f"{rets.median():+.1f}%")
                st.metric("Best", f"{rets.max():+.1f}%")
                st.metric("Worst", f"{rets.min():+.1f}%")
                st.metric("Companies with Data", f"{len(rets)}")
                # 1-year stats if different from best period
                if _best_period_col != "return_1y":
                    rets_1y = signal_df["return_1y"].dropna()
                    if not rets_1y.empty:
                        st.markdown("---")
                        st.caption("1-Year (where available)")
                        st.metric("Median 1Y Return", f"{rets_1y.median():+.1f}%")

        # ── Company-level table — label N/A cells meaningfully ────────────
        st.markdown("**Company-Level Returns**")
        ret_disp = signal_df.copy()
        ret_disp["ticker"] = ret_disp["ticker"].fillna("N/A")
        ret_disp["Signal Date"] = (
            pd.to_datetime(ret_disp["signal_date"], errors="coerce")
            .dt.strftime("%Y-%m-%d")
            .fillna("N/A")
        )
        ret_disp["Total Spend"] = ret_disp["total_spend"].apply(format_currency)
        for _col, (_lbl, _tgt) in _period_meta.items():
            _tgt_str_short = _tgt.strftime("%b '%y")
            _is_future = _tgt > _today
            ret_disp[_col] = ret_disp[_col].apply(
                lambda x, _f=_is_future, _ts=_tgt_str_short:
                    f"{x:+.1f}%" if pd.notna(x)
                    else (f"pending (~{_ts})" if _f else "refresh")
            )
        ret_disp = ret_disp[[
            "company_name", "ticker", "sector", "Signal Date", "Total Spend",
            "return_1m", "return_3m", "return_6m", "return_1y",
        ]]
        ret_disp.columns = [
            "Company", "Ticker", "Sector", "Signal Date", f"Q4 Spend ({_signal_year} Signal)",
            "1-Month", "3-Month", "6-Month", "1-Year",
        ]
        st.dataframe(ret_disp, use_container_width=True, hide_index=True, height=400)

    st.markdown("---")

    # ── Section 3: Strategy vs S&P 500 Benchmark ──────────────────────────
    st.markdown("#### Strategy vs. S&P 500 — Annual Returns")
    _render_benchmark_section(
        fetcher.db_path,
        cache_buster=st.session_state["cache_buster"],
        key_prefix="tab3_bench",
    )

    st.markdown("---")

    # ── Section 4: Production Go/No-Go Truth Panel ──────────────────────
    st.markdown("#### Production Go / No-Go Truth Panel")
    st.caption(
        "Hard deployment gates on the locked production model: "
        "net alpha, outlier robustness, sector-neutral robustness, and statistical confidence."
    )

    prod_primary_factor = str(
        getattr(config, "PRODUCTION_PRIMARY_FACTOR", "hist_spend_z")
    )
    prod_fallback_factor = str(
        getattr(config, "PRODUCTION_FALLBACK_FACTOR", "accel_score")
    )
    prod_top_n = int(getattr(config, "PRODUCTION_TOP_N", 10))
    prod_min_usable = int(getattr(config, "PRODUCTION_MIN_USABLE_NAMES", 5))
    sector_k = int(getattr(config, "SECTOR_NEUTRAL_TOP_K_DEFAULT", 1))

    go_summary, go_yearly = get_production_go_nogo_metrics(
        fetcher.db_path,
        top_n=prod_top_n,
        primary_factor=prod_primary_factor,
        fallback_factor=prod_fallback_factor,
        refresh_token=st.session_state["cache_buster"],
        min_spend_m=min_spend,
        min_usable_names=prod_min_usable,
        sector_k=sector_k,
    )

    if not go_summary.get("ready"):
        st.info(f"Go/No-Go panel unavailable: {go_summary.get('reason', 'No data.')}")
    else:
        if go_summary.get("spx_error"):
            st.warning(f"S&P benchmark note: {go_summary['spx_error']}")

        def _pf(flag: bool) -> str:
            return "PASS" if bool(flag) else "FAIL"

        def _pct(v, signed=True):
            if v is None or pd.isna(v):
                return "N/A"
            return f"{v:+.2f}%" if signed else f"{v:.1f}%"

        g1, g2, g3, g4, g5 = st.columns(5)
        g1.metric(
            "Net Alpha Gate",
            _pf(go_summary.get("pass_net")),
            (
                f"{_pct(go_summary.get('avg_alpha_net'))}, "
                f"{_pct(go_summary.get('win_rate_net'), signed=False)} wins"
            ),
        )
        g2.metric(
            "Robustness Gate",
            _pf(go_summary.get("pass_robust")),
            (
                f"{_pct(go_summary.get('avg_alpha_ex_best'))}, "
                f"{_pct(go_summary.get('win_rate_ex_best'), signed=False)} wins"
            ),
        )
        g3.metric(
            "Sector-Neutral Gate",
            _pf(go_summary.get("pass_sector")),
            (
                f"{_pct(go_summary.get('avg_sector_neutral_alpha'))}, "
                f"{_pct(go_summary.get('win_rate_sector_neutral'), signed=False)} wins"
            ),
        )
        g4.metric(
            "Significance Gate",
            _pf(go_summary.get("pass_significance")),
            (
                f"CI [{go_summary.get('ci_low', float('nan')):+.2f}%, "
                f"{go_summary.get('ci_high', float('nan')):+.2f}%], "
                f"p={go_summary.get('p_binom', float('nan')):.3f}"
            ),
        )
        g5.metric("Decision", "GO" if go_summary.get("go_live") else "NO-GO")

        if go_summary.get("go_live"):
            st.success(
                "Production model passes all deployment gates under current thresholds."
            )
        else:
            st.error(
                "Production model fails one or more deployment gates. "
                "Treat as research-only until gates pass."
            )

        st.caption(
            "Thresholds: "
            f"net alpha >= {go_summary.get('threshold_net_alpha', 0):.2f}%, "
            f"net win rate >= {go_summary.get('threshold_win_rate', 0):.1f}%, "
            f"robust alpha >= {go_summary.get('threshold_robust_alpha', 0):.2f}%, "
            f"sector-neutral alpha >= {go_summary.get('threshold_sector_alpha', 0):.2f}%, "
            f"binomial p <= {go_summary.get('threshold_p', 0):.2f}, "
            "and bootstrap CI lower bound > 0."
        )

        with st.expander("Go/No-Go Annual Diagnostics", expanded=False):
            show_go = go_yearly.copy()
            for c in [
                "top_return_1y",
                "spx_return",
                "alpha_vs_spx_1y",
                "net_alpha_vs_spx_1y",
                "alpha_ex_best_1y",
                "turnover_pct",
                "cost_pct",
            ]:
                if c in show_go.columns:
                    if c in {"turnover_pct", "cost_pct"}:
                        show_go[c] = show_go[c].apply(
                            lambda v: f"{v:.2f}%" if pd.notna(v) else "N/A"
                        )
                    else:
                        show_go[c] = show_go[c].apply(
                            lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                        )
            show_go = show_go.rename(
                columns={
                    "signal_year": "Signal Year",
                    "year": "Hold Year",
                    "model_used": "Model",
                    "fallback_used": "Fallback",
                    "top_return_1y": "Top 1Y",
                    "spx_return": "SPX 1Y",
                    "alpha_vs_spx_1y": "Raw Alpha",
                    "net_alpha_vs_spx_1y": "Net Alpha",
                    "alpha_ex_best_1y": "Alpha ex-Best",
                    "turnover_pct": "Turnover",
                    "cost_pct": "Cost Drag",
                }
            )
            st.dataframe(show_go, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── Section 5: Production Strategy + Factor Lab ──────────────────────
    st.markdown("#### Production Strategy (Primary + Fallback)")
    prod_hold_year = int(selected_year) + 1

    st.caption(
        f"Default production model uses **{prod_primary_factor}** (Top {prod_top_n}) "
        f"with fallback to **{prod_fallback_factor}** when primary coverage is thin."
    )
    st.caption(
        f"Year control is interpreted as signal year here: "
        f"{int(selected_year)} filings -> hold year {prod_hold_year}."
    )

    prod_live_df, prod_live_meta = get_production_strategy_holdings(
        fetcher.db_path,
        hold_year=prod_hold_year,
        top_n=prod_top_n,
        primary_factor=prod_primary_factor,
        fallback_factor=prod_fallback_factor,
        refresh_token=st.session_state["cache_buster"],
        min_spend_m=min_spend,
        min_usable_names=prod_min_usable,
    )

    if prod_live_df.empty:
        st.info(
            f"No production holdings for hold year `{prod_hold_year}` "
            f"(signal year `{prod_hold_year - 1}`). "
            "Check ticker coverage and return data freshness."
        )
    else:
        pr1, pr2, pr3, pr4 = st.columns(4)
        with pr1:
            st.metric("Hold Year", f"{prod_hold_year}")
        with pr2:
            st.metric("Signal Year", f"{int(prod_live_meta.get('signal_year', prod_hold_year - 1))}")
        with pr3:
            st.metric("Model Used", str(prod_live_meta.get("model_used", "N/A")))
        with pr4:
            st.metric("Selected Names", f"{int(prod_live_meta.get('selected_n', len(prod_live_df)))}")

        if prod_live_meta.get("reason"):
            st.caption(str(prod_live_meta.get("reason")))

        prod_show = prod_live_df.copy()
        prod_show["annual_spend"] = prod_show["annual_spend"].apply(format_currency)
        for c in ["hist_spend_z", "accel_score"]:
            if c in prod_show.columns:
                prod_show[c] = prod_show[c].apply(
                    lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                )
        for c in ["return_1m", "return_3m", "return_6m", "return_1y"]:
            if c in prod_show.columns:
                prod_show[c] = prod_show[c].apply(
                    lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                )
        if "signal_date" in prod_show.columns:
            prod_show["signal_date"] = (
                pd.to_datetime(prod_show["signal_date"], errors="coerce")
                .dt.strftime("%Y-%m-%d")
                .fillna("N/A")
            )
        prod_show = prod_show.rename(
            columns={
                "company_name": "Company",
                "ticker": "Ticker",
                "sector": "Sector",
                "signal_date": "Signal Date",
                "annual_spend": "Annual Spend",
                "hist_spend_z": "Hist Z",
                "accel_score": "Composite",
                "return_1m": "Fwd 1M",
                "return_3m": "Fwd 3M",
                "return_6m": "Fwd 6M",
                "return_1y": "Fwd 1Y",
            }
        )
        keep_cols = [
            c
            for c in [
                "Company",
                "Ticker",
                "Sector",
                "Signal Date",
                "Annual Spend",
                "Hist Z",
                "Composite",
                "Fwd 1M",
                "Fwd 3M",
                "Fwd 6M",
                "Fwd 1Y",
            ]
            if c in prod_show.columns
        ]
        st.dataframe(
            prod_show[keep_cols],
            use_container_width=True,
            hide_index=True,
            height=300,
        )

    prod_bt_df, prod_bt_detail_df = get_production_strategy_backtest(
        fetcher.db_path,
        top_n=prod_top_n,
        primary_factor=prod_primary_factor,
        fallback_factor=prod_fallback_factor,
        refresh_token=st.session_state["cache_buster"],
        min_spend_m=min_spend,
        min_usable_names=prod_min_usable,
    )
    if not prod_bt_df.empty:
        prod_eval = prod_bt_df.dropna(subset=["alpha_vs_spx_1y"]).copy()
        pm1, pm2, pm3, pm4 = st.columns(4)
        pm1.metric("Backtest Years", f"{len(prod_eval):,}")
        pm2.metric(
            "Avg Alpha vs SPX (1Y)",
            (
                f"{prod_eval['alpha_vs_spx_1y'].mean():+.2f}%"
                if not prod_eval.empty
                else "N/A"
            ),
        )
        pm3.metric(
            "Win Rate vs SPX",
            (
                f"{(prod_eval['alpha_vs_spx_1y'] > 0).mean() * 100.0:.1f}%"
                if not prod_eval.empty
                else "N/A"
            ),
        )
        pm4.metric(
            "Fallback Years",
            f"{int(prod_bt_df['fallback_used'].fillna(False).sum())}",
        )

        fig_prod = go.Figure()
        fig_prod.add_trace(
            go.Bar(
                x=prod_bt_df["year"],
                y=prod_bt_df["alpha_vs_spx_1y"],
                marker_color=[
                    "#f59e0b" if bool(v) else "#34d399"
                    for v in prod_bt_df["fallback_used"].fillna(False).tolist()
                ],
                text=[
                    f"{v:+.1f}%" if pd.notna(v) else "N/A"
                    for v in prod_bt_df["alpha_vs_spx_1y"]
                ],
                textposition="outside",
                name="Alpha vs SPX (1Y)",
            )
        )
        fig_prod.add_hline(y=0, line_color="#6b7280", line_width=1)
        fig_prod.update_layout(
            xaxis=dict(title="Hold Year", dtick=1, gridcolor="#2d3748"),
            yaxis=dict(title="Alpha vs SPX 1Y (%)", gridcolor="#2d3748"),
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font=dict(color="#ffffff", size=12),
            height=320,
            margin=dict(l=0, r=20, t=20, b=0),
            showlegend=False,
        )
        st.plotly_chart(fig_prod, use_container_width=True, key="prod_alpha_vs_spx_chart")
        st.caption(
            "Bar color: green = primary model, amber = fallback model."
        )

        with st.expander("Production Strategy Annual Table", expanded=False):
            show_prod_bt = prod_bt_df.copy()
            for c in [
                "top_return_1m",
                "top_return_3m",
                "top_return_6m",
                "top_return_1y",
                "universe_return_1y",
                "alpha_return_1y",
                "spx_return",
                "alpha_vs_spx_1y",
                "return_coverage_pct",
            ]:
                if c in show_prod_bt.columns:
                    show_prod_bt[c] = show_prod_bt[c].apply(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) and c != "return_coverage_pct"
                        else (f"{v:.1f}%" if pd.notna(v) else "N/A")
                    )
            show_prod_bt = show_prod_bt.rename(
                columns={
                    "signal_year": "Signal Year",
                    "year": "Hold Year",
                    "model_used": "Model",
                    "fallback_used": "Fallback Used",
                    "n_universe": "Universe N",
                    "n_selected": "Selected N",
                    "top_return_1y": "Top 1Y",
                    "universe_return_1y": "Univ 1Y",
                    "alpha_return_1y": "Alpha vs Univ 1Y",
                    "spx_return": "SPX",
                    "alpha_vs_spx_1y": "Alpha vs SPX 1Y",
                    "return_coverage_pct": "1Y Coverage",
                }
            )
            st.dataframe(show_prod_bt, use_container_width=True, hide_index=True)

        if not prod_bt_detail_df.empty:
            st.download_button(
                "Download Production Holdings Backtest CSV",
                data=prod_bt_detail_df.to_csv(index=False).encode("utf-8"),
                file_name="production_strategy_holdings.csv",
                mime="text/csv",
                key="prod_holdings_dl",
            )

    st.markdown("---")
    st.markdown("#### Acceleration Factor Lab (Research)")
    st.caption(
        "Tests acceleration/spike features rather than raw spend levels: "
        "YoY%, QoQ%, spend z-score vs history, sector-normalized spike, and "
        "YoY change in spend/market-cap ratio."
    )

    acc_c1, acc_c2 = st.columns([2, 1])
    with acc_c1:
        accel_top_n = st.slider(
            "Top N acceleration names (per year)",
            min_value=5,
            max_value=100,
            value=int(getattr(config, "ACCELERATION_TOP_N_DEFAULT", 20)),
            step=5,
            key="accel_top_n",
        )
    with acc_c2:
        accel_sector_k = st.slider(
            "Sector-neutral k/sector",
            min_value=1,
            max_value=3,
            value=int(getattr(config, "SECTOR_NEUTRAL_TOP_K_DEFAULT", 1)),
            step=1,
            key="accel_sector_k",
            help="Long top-k and short bottom-k acceleration names per sector.",
        )

    accel_feat_df = get_acceleration_features(
        fetcher.db_path,
        selected_year,
        refresh_token=st.session_state["cache_buster"],
        min_spend_m=min_spend,
        ticker_only=True,
    )

    if accel_feat_df.empty:
        st.info(
            f"No acceleration feature set available for {selected_year} "
            f"at min spend ${min_spend:.1f}M."
        )
    else:
        accel_ret_df = get_signal_returns(
            fetcher.db_path,
            selected_year,
            refresh_token=st.session_state["cache_buster"],
        )
        if not accel_ret_df.empty:
            accel_now = accel_feat_df.merge(
                accel_ret_df[["ticker", "return_3m", "return_6m", "return_1y"]],
                on="ticker",
                how="left",
            )
        else:
            accel_now = accel_feat_df.copy()
            accel_now["return_3m"] = None
            accel_now["return_6m"] = None
            accel_now["return_1y"] = None

        accel_top_now = accel_now.sort_values(
            ["accel_score", "annual_spend"], ascending=[False, False]
        ).head(int(accel_top_n))

        if not accel_top_now.empty:
            a1, a2, a3, a4 = st.columns(4)
            with a1:
                st.metric("Scored Universe", f"{len(accel_now):,}")
            with a2:
                st.metric("Top-N Selected", f"{len(accel_top_now):,}")
            with a3:
                med_score = accel_now["accel_score"].dropna().median()
                st.metric("Median Accel Score", f"{med_score:.1f}" if pd.notna(med_score) else "N/A")
            with a4:
                med_z = accel_now["hist_spend_z"].dropna().median()
                st.metric("Median Spend Z", f"{med_z:.2f}" if pd.notna(med_z) else "N/A")

            st.markdown("**Top Acceleration Names (Selected Year)**")
            disp_acc = accel_top_now.copy()
            disp_acc["annual_spend"] = disp_acc["annual_spend"].apply(format_currency)
            disp_acc["market_cap"] = disp_acc["market_cap"].apply(format_currency)
            for c in ["yoy_pct", "qoq_pct", "mcap_ratio_yoy_pct", "return_3m", "return_6m", "return_1y"]:
                disp_acc[c] = disp_acc[c].apply(
                    lambda v: f"{v:+.1f}%" if pd.notna(v) else "N/A"
                )
            for c in ["hist_spend_z", "sector_spike_z", "accel_score"]:
                disp_acc[c] = disp_acc[c].apply(
                    lambda v: f"{v:.2f}" if pd.notna(v) else "N/A"
                )
            disp_acc = disp_acc.rename(
                columns={
                    "company_name": "Company",
                    "ticker": "Ticker",
                    "sector": "Sector",
                    "annual_spend": "Spend",
                    "market_cap": "Market Cap",
                    "yoy_pct": "YoY%",
                    "qoq_pct": "QoQ%",
                    "hist_spend_z": "Spend Z",
                    "sector_spike_z": "Sector Spike Z",
                    "mcap_ratio_yoy_pct": "d(Spend/MCap) YoY%",
                    "accel_score": "Accel Score",
                    "return_3m": "Fwd 3M",
                    "return_6m": "Fwd 6M",
                    "return_1y": "Fwd 1Y",
                }
            )
            st.dataframe(
                disp_acc[
                    [
                        "Company",
                        "Ticker",
                        "Sector",
                        "Spend",
                        "Market Cap",
                        "Accel Score",
                        "YoY%",
                        "QoQ%",
                        "Spend Z",
                        "Sector Spike Z",
                        "d(Spend/MCap) YoY%",
                        "Fwd 3M",
                        "Fwd 6M",
                        "Fwd 1Y",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
                height=360,
            )

    # Multi-year event-window backtest
    accel_bt_df, accel_detail_df = get_acceleration_event_backtest(
        fetcher.db_path,
        top_n=int(accel_top_n),
        refresh_token=st.session_state["cache_buster"],
        min_spend_m=min_spend,
    )

    if not accel_bt_df.empty:
        st.markdown("**Event-Window Backtest (Top-N Acceleration vs Universe)**")

        kb1, kb2, kb3, kb4 = st.columns(4)
        kb1.metric("Years", f"{len(accel_bt_df)}")
        kb2.metric(
            "Avg Alpha 3M",
            (
                f"{accel_bt_df['alpha_return_3m'].dropna().mean():+.2f}%"
                if accel_bt_df["alpha_return_3m"].notna().any()
                else "N/A"
            ),
        )
        kb3.metric(
            "Avg Alpha 6M",
            (
                f"{accel_bt_df['alpha_return_6m'].dropna().mean():+.2f}%"
                if accel_bt_df["alpha_return_6m"].notna().any()
                else "N/A"
            ),
        )
        kb4.metric(
            "Avg Alpha 1Y",
            (
                f"{accel_bt_df['alpha_return_1y'].dropna().mean():+.2f}%"
                if accel_bt_df["alpha_return_1y"].notna().any()
                else "N/A"
            ),
        )

        fig_acc = go.Figure()
        fig_acc.add_trace(
            go.Scatter(
                x=accel_bt_df["year"],
                y=accel_bt_df["alpha_return_3m"],
                mode="lines+markers",
                name="Alpha 3M",
                line=dict(color="#60a5fa", width=2),
            )
        )
        fig_acc.add_trace(
            go.Scatter(
                x=accel_bt_df["year"],
                y=accel_bt_df["alpha_return_6m"],
                mode="lines+markers",
                name="Alpha 6M",
                line=dict(color="#34d399", width=2),
            )
        )
        fig_acc.add_trace(
            go.Scatter(
                x=accel_bt_df["year"],
                y=accel_bt_df["alpha_return_1y"],
                mode="lines+markers",
                name="Alpha 1Y",
                line=dict(color="#fbbf24", width=2),
            )
        )
        fig_acc.add_hline(y=0, line_color="#6b7280", line_width=1)
        fig_acc.update_layout(
            xaxis=dict(title="Hold Year", dtick=1, gridcolor="#2d3748"),
            yaxis=dict(title="Top-N minus Universe Return (%)", gridcolor="#2d3748"),
            plot_bgcolor="#0e1117",
            paper_bgcolor="#0e1117",
            font=dict(color="#ffffff", size=12),
            legend=dict(
                bgcolor="#1a1d24",
                bordercolor="#4a5568",
                borderwidth=1,
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
            ),
            height=360,
            margin=dict(l=0, r=30, t=40, b=0),
        )
        st.plotly_chart(fig_acc, use_container_width=True, key="accel_event_window_chart")

        with st.expander("Acceleration Event Backtest Table", expanded=False):
            show_bt = accel_bt_df.copy()
            pct_cols = [
                c
                for c in show_bt.columns
                if c.startswith("top_return_")
                or c.startswith("universe_return_")
                or c.startswith("alpha_return_")
                or c in {"spx_return", "alpha_vs_spx_1y"}
            ]
            for c in pct_cols:
                show_bt[c] = show_bt[c].apply(lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A")
            show_bt = show_bt.rename(
                columns={
                    "signal_year": "Signal Year",
                    "year": "Hold Year",
                    "n_universe": "Universe N",
                    "n_top": "Top N",
                    "top_return_3m": "Top 3M",
                    "top_return_6m": "Top 6M",
                    "top_return_1y": "Top 1Y",
                    "universe_return_3m": "Univ 3M",
                    "universe_return_6m": "Univ 6M",
                    "universe_return_1y": "Univ 1Y",
                    "alpha_return_3m": "Alpha 3M",
                    "alpha_return_6m": "Alpha 6M",
                    "alpha_return_1y": "Alpha 1Y",
                    "alpha_vs_spx_1y": "Alpha vs SPX 1Y",
                }
            )
            st.dataframe(show_bt, use_container_width=True, hide_index=True)

        # Sector-neutral long/short
        sn_df = get_sector_neutral_acceleration_backtest(
            fetcher.db_path,
            top_k_per_sector=int(accel_sector_k),
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
        )
        if not sn_df.empty:
            st.markdown("**Sector-Neutral Long/Short Test**")
            ks1, ks2, ks3 = st.columns(3)
            ks1.metric(
                "Avg LS 3M",
                (
                    f"{sn_df['ls_return_3m'].dropna().mean():+.2f}%"
                    if sn_df["ls_return_3m"].notna().any()
                    else "N/A"
                ),
            )
            ks2.metric(
                "Avg LS 6M",
                (
                    f"{sn_df['ls_return_6m'].dropna().mean():+.2f}%"
                    if sn_df["ls_return_6m"].notna().any()
                    else "N/A"
                ),
            )
            ks3.metric(
                "Avg LS 1Y",
                (
                    f"{sn_df['ls_return_1y'].dropna().mean():+.2f}%"
                    if sn_df["ls_return_1y"].notna().any()
                    else "N/A"
                ),
            )
            with st.expander("Sector-Neutral Annual Table", expanded=False):
                show_sn = sn_df.copy()
                for c in [col for col in show_sn.columns if "return_" in col]:
                    show_sn[c] = show_sn[c].apply(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                    )
                show_sn = show_sn.rename(
                    columns={
                        "signal_year": "Signal Year",
                        "year": "Hold Year",
                        "n_sectors": "Sectors",
                        "n_long": "Long N",
                        "n_short": "Short N",
                        "ls_return_3m": "LS 3M",
                        "ls_return_6m": "LS 6M",
                        "ls_return_1y": "LS 1Y",
                    }
                )
                st.dataframe(show_sn, use_container_width=True, hide_index=True)

        # Regime filter summary
        regime_merged, regime_summary = get_regime_conditioned_acceleration_performance(
            fetcher.db_path,
            top_n=int(accel_top_n),
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
        )
        if regime_summary.empty:
            st.info(
                "Regime-conditioned summary unavailable (likely due missing macro proxy data)."
            )
        else:
            st.markdown("**Regime-Conditioned Performance**")
            st.caption(
                "Recession risk uses a proxy (10Y-3M term spread deterioration/inversion), "
                "not a model-implied probability."
            )
            show_reg = regime_summary.copy()
            for c in [
                "Avg Top 3M",
                "Avg Top 6M",
                "Avg Top 1Y",
                "Avg Alpha 1Y (vs Univ)",
                "Avg Alpha 1Y (vs SPX)",
            ]:
                show_reg[c] = show_reg[c].apply(
                    lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                )
            st.dataframe(show_reg, use_container_width=True, hide_index=True)

        st.markdown("**Robustness Guardrails**")

        # Rolling stability windows
        rolling_df = get_acceleration_rolling_windows(
            fetcher.db_path,
            top_n=int(accel_top_n),
            window_years=3,
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
        )
        if rolling_df.empty:
            st.info("Rolling 3-year stability windows unavailable yet.")
        else:
            st.caption("Rolling 3-year windows (signal years) to test temporal stability.")
            fig_roll = go.Figure()
            fig_roll.add_trace(
                go.Scatter(
                    x=rolling_df["window_label"],
                    y=rolling_df["avg_alpha_1y"],
                    mode="lines+markers",
                    name="Avg Alpha 1Y",
                    line=dict(color="#60a5fa", width=2),
                )
            )
            fig_roll.add_hline(y=0, line_color="#6b7280", line_width=1)
            fig_roll.update_layout(
                xaxis=dict(title="Signal-Year Window", gridcolor="#2d3748"),
                yaxis=dict(title="Avg 1Y Alpha (%)", gridcolor="#2d3748"),
                plot_bgcolor="#0e1117",
                paper_bgcolor="#0e1117",
                font=dict(color="#ffffff", size=12),
                height=280,
                margin=dict(l=0, r=20, t=20, b=0),
            )
            st.plotly_chart(fig_roll, use_container_width=True, key="accel_roll_stability")
            show_roll = rolling_df.copy()
            for c in ["avg_alpha_3m", "avg_alpha_6m", "avg_alpha_1y", "win_rate_1y"]:
                show_roll[c] = show_roll[c].apply(
                    lambda v: f"{v:+.2f}%" if pd.notna(v) and c != "win_rate_1y"
                    else (f"{v:.1f}%" if pd.notna(v) else "N/A")
                )
            show_roll = show_roll.rename(
                columns={
                    "window_label": "Window",
                    "n_years": "Years",
                    "avg_alpha_3m": "Avg Alpha 3M",
                    "avg_alpha_6m": "Avg Alpha 6M",
                    "avg_alpha_1y": "Avg Alpha 1Y",
                    "win_rate_1y": "1Y Win Rate",
                }
            )
            st.dataframe(
                show_roll[["Window", "Years", "Avg Alpha 3M", "Avg Alpha 6M", "Avg Alpha 1Y", "1Y Win Rate"]],
                use_container_width=True,
                hide_index=True,
            )

        # Out-of-sample split
        oos_train_start = int(getattr(config, "OOS_TRAIN_START_YEAR", 2019))
        oos_train_end = int(getattr(config, "OOS_TRAIN_END_YEAR", 2022))
        oos_test_start = int(getattr(config, "OOS_TEST_START_YEAR", 2023))
        oos_test_end = int(getattr(config, "OOS_TEST_END_YEAR", 2026))

        oos_annual, oos_summary, oos_weights = get_acceleration_oos_split_backtest(
            fetcher.db_path,
            top_n=int(accel_top_n),
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
            train_start=oos_train_start,
            train_end=oos_train_end,
            test_start=oos_test_start,
            test_end=oos_test_end,
        )
        if oos_annual.empty:
            st.info("Out-of-sample split results unavailable (insufficient train/test return coverage).")
        else:
            st.caption(
                f"OOS split: train={oos_train_start}-{oos_train_end}, "
                f"test={oos_test_start}-{oos_test_end}. "
                "Model weights are fit on train only and then frozen."
            )
            if not oos_summary.empty:
                show_oos_sum = oos_summary.copy()
                for c in ["Avg Alpha 3M", "Avg Alpha 6M", "Avg Alpha 1Y", "1Y Win Rate"]:
                    show_oos_sum[c] = show_oos_sum[c].apply(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) and c != "1Y Win Rate"
                        else (f"{v:.1f}%" if pd.notna(v) else "N/A")
                    )
                st.dataframe(show_oos_sum, use_container_width=True, hide_index=True)
            if not oos_weights.empty:
                with st.expander("Fitted Train Weights", expanded=False):
                    show_w = oos_weights.copy()
                    show_w["weight"] = show_w["weight"].apply(lambda v: f"{v:+.4f}")
                    st.dataframe(show_w.rename(columns={"feature": "Feature", "weight": "Weight"}),
                                 use_container_width=True, hide_index=True)
            with st.expander("Out-of-Sample Annual Table", expanded=False):
                show_oos = oos_annual.copy()
                for c in [col for col in show_oos.columns if "return_" in col or "alpha_" in col]:
                    show_oos[c] = show_oos[c].apply(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                    )
                show_oos = show_oos.rename(
                    columns={
                        "signal_year": "Signal Year",
                        "year": "Hold Year",
                        "split": "Split",
                        "n_universe": "Universe N",
                        "n_top": "Top N",
                        "alpha_return_3m": "Alpha 3M",
                        "alpha_return_6m": "Alpha 6M",
                        "alpha_return_1y": "Alpha 1Y",
                    }
                )
                st.dataframe(show_oos, use_container_width=True, hide_index=True)

        # Simplicity check
        simp_annual, simp_summary = get_acceleration_simplicity_check(
            fetcher.db_path,
            top_n=int(accel_top_n),
            refresh_token=st.session_state["cache_buster"],
            min_spend_m=min_spend,
            simple_metric="yoy_pct",
        )
        if simp_summary.empty:
            st.info("Simplicity check unavailable (not enough overlap between score and YoY universes).")
        else:
            st.caption(
                "Simplicity check: composite acceleration score vs single-metric YoY% selection."
            )
            show_simp = simp_summary.copy()
            for c in [
                "Avg overlap %",
                "Avg (Simple-Comp) 3M",
                "Avg (Simple-Comp) 6M",
                "Avg (Simple-Comp) 1Y",
                "Tolerance (abs diff 1Y)",
            ]:
                show_simp[c] = show_simp[c].apply(
                    lambda v: f"{v:.1f}%" if pd.notna(v) and c == "Avg overlap %"
                    else (f"{v:+.2f}%" if pd.notna(v) else "N/A")
                )
            st.dataframe(show_simp, use_container_width=True, hide_index=True)
            with st.expander("Simplicity Annual Table", expanded=False):
                show_sa = simp_annual.copy()
                for c in [col for col in show_sa.columns if "return_" in col or "simple_minus_comp" in col]:
                    show_sa[c] = show_sa[c].apply(
                        lambda v: f"{v:+.2f}%" if pd.notna(v) else "N/A"
                    )
                show_sa = show_sa.rename(
                    columns={
                        "signal_year": "Signal Year",
                        "year": "Hold Year",
                        "overlap_pct": "Overlap %",
                        "simple_minus_comp_return_3m": "Simple-Comp 3M",
                        "simple_minus_comp_return_6m": "Simple-Comp 6M",
                        "simple_minus_comp_return_1y": "Simple-Comp 1Y",
                    }
                )
                st.dataframe(show_sa, use_container_width=True, hide_index=True)

        if not accel_detail_df.empty:
            st.download_button(
                "Download Acceleration Constituents CSV",
                data=accel_detail_df.to_csv(index=False).encode("utf-8"),
                file_name=f"acceleration_constituents_{selected_year}.csv",
                mime="text/csv",
                key="accel_dl_detail",
            )

    st.markdown("---")

    # ── Section 5: Historical Data Coverage (moved from top) ───────────────
    with st.expander("Historical Data Coverage", expanded=False):
        st.caption("Data completeness metrics stored in your local database.")
        yearly_summary = get_yearly_summary(fetcher.db_path, st.session_state["cache_buster"])

        if yearly_summary.empty:
            st.info("No yearly summary data available yet.")
        else:
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Total Lobbying Spend by Year**")
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=yearly_summary["year"],
                    y=(yearly_summary["total_lobbying_spend"] / 1_000_000),
                    marker_color="#60a5fa",
                    name="Spend ($M)",
                ))
                fig.update_layout(
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    xaxis=dict(gridcolor="#2d3748"),
                    yaxis=dict(gridcolor="#2d3748", title="Spend ($M)"),
                    height=300,
                )
                st.plotly_chart(fig, use_container_width=True)

            with col2:
                st.markdown("**Entity Coverage by Year**")
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=yearly_summary["year"], y=yearly_summary["unique_entities"],
                    name="Unique Entities", marker_color="#34d399",
                ))
                fig.add_trace(go.Bar(
                    x=yearly_summary["year"], y=yearly_summary["mapped_entities"],
                    name="Mapped Tickers", marker_color="#fbbf24",
                ))
                fig.update_layout(
                    barmode="group",
                    plot_bgcolor="#0e1117", paper_bgcolor="#0e1117",
                    font=dict(color="#ffffff", size=12),
                    xaxis=dict(gridcolor="#2d3748"),
                    yaxis=dict(gridcolor="#2d3748", title="Count"),
                    legend=dict(bgcolor="#1a1d24", bordercolor="#4a5568", borderwidth=1),
                    height=300,
                )
                st.plotly_chart(fig, use_container_width=True)

            coverage_df = yearly_summary.copy()
            coverage_df["Total Lobbying Spend"] = coverage_df["total_lobbying_spend"].apply(format_currency)
            coverage_df["Ticker Match Rate"] = coverage_df["ticker_match_rate"].apply(lambda x: f"{x:.2f}%")
            coverage_df["Market Cap Coverage"] = coverage_df["market_cap_coverage"].apply(lambda x: f"{x:.2f}%")
            coverage_df["Entity Match Rate"] = coverage_df["entity_match_rate"].apply(lambda x: f"{x:.2f}%")
            coverage_df["Entity MCap Coverage"] = coverage_df["entity_mcap_coverage"].apply(lambda x: f"{x:.2f}%")
            coverage_df = coverage_df[
                [
                    "year",
                    "unique_entities",
                    "mapped_entities",
                    "with_market_cap_entities",
                    "total_rows",
                    "mapped_rows",
                    "with_market_cap_rows",
                    "Total Lobbying Spend",
                    "Entity Match Rate",
                    "Entity MCap Coverage",
                    "Ticker Match Rate",
                    "Market Cap Coverage",
                ]
            ]
            coverage_df.columns = [
                "Year",
                "Unique Entities",
                "Mapped Tickers",
                "Entities With MCap",
                "Total Rows",
                "Rows With Ticker",
                "Rows With MCap",
                "Total Lobbying Spend",
                "Entity Match Rate",
                "Entity MCap Coverage",
                "Row Match Rate",
                "Row MCap Coverage",
            ]
            st.dataframe(
                coverage_df.sort_values("Year", ascending=False),
                use_container_width=True, hide_index=True, height=250,
            )
