"""
Policy-risk regime features and regime-conditioned acceleration performance.
"""

import pandas as pd
import streamlit as st

import config

from dashboard.acceleration import get_acceleration_event_backtest
from dashboard.benchmark import _q4_signal_window


@st.cache_data
def get_policy_risk_regime_features(signal_years, refresh_token: int = 0):
    """
    Build macro/policy-risk regime flags at each signal date:
      - SPY below 200DMA
      - VIX above threshold
      - Credit spreads proxy widening (LQD/HYG ratio rising)
      - Recession-risk proxy rising (10Y-3M term spread deteriorating/inverted)
    """
    import yfinance as yf

    if not signal_years:
        return pd.DataFrame()

    min_signal_year = int(min(signal_years))
    max_signal_year = int(max(signal_years))
    start = f"{min_signal_year - 2}-01-01"
    end = f"{max_signal_year + 2}-12-31"

    symbols = ["SPY", "^VIX", "LQD", "HYG", "^TNX", "^IRX"]
    market = {}
    for sym in symbols:
        try:
            df = yf.download(
                sym,
                start=start,
                end=end,
                progress=False,
                auto_adjust=True,
            )
            if df.empty:
                continue
            close = df["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close.index = pd.to_datetime(close.index).tz_localize(None)
            market[sym] = close.sort_index()
        except Exception:
            continue

    if "SPY" not in market:
        return pd.DataFrame()

    spy = market["SPY"].copy()
    spy_200dma = spy.rolling(200, min_periods=180).mean()
    vix = market.get("^VIX")
    lqd = market.get("LQD")
    hyg = market.get("HYG")
    tnx = market.get("^TNX")
    irx = market.get("^IRX")

    # Credit-spread proxy: safer credit (LQD) outperforming high-yield (HYG)
    # implies spread widening / risk-off conditions.
    credit_ratio = None
    credit_delta = None
    credit_lb = int(getattr(config, "REGIME_CREDIT_LOOKBACK_DAYS", 63))
    if lqd is not None and hyg is not None:
        common_idx = lqd.index.intersection(hyg.index)
        if not common_idx.empty:
            ratio = (lqd.reindex(common_idx) / hyg.reindex(common_idx)).dropna()
            if not ratio.empty:
                credit_ratio = ratio
                credit_delta = ratio - ratio.shift(credit_lb)

    term_spread = None
    term_delta = None
    if tnx is not None and irx is not None:
        common_idx = tnx.index.intersection(irx.index)
        if not common_idx.empty:
            spread = (tnx.reindex(common_idx) - irx.reindex(common_idx)).dropna()
            if not spread.empty:
                term_spread = spread
                term_delta = spread - spread.shift(63)

    def _last_val(series, asof_ts):
        if series is None or series.empty:
            return None
        data = series[series.index <= asof_ts]
        if data.empty:
            return None
        return float(data.iloc[-1])

    vix_threshold = float(getattr(config, "REGIME_VIX_THRESHOLD", 25.0))
    rows = []
    for year in sorted(set(int(y) for y in signal_years)):
        signal_date, _, _ = _q4_signal_window(year, window_days=46)
        signal_ts = pd.to_datetime(signal_date)

        spy_px = _last_val(spy, signal_ts)
        spy_dma = _last_val(spy_200dma, signal_ts)
        vix_val = _last_val(vix, signal_ts)
        credit_d = _last_val(credit_delta, signal_ts)
        term_val = _last_val(term_spread, signal_ts)
        term_d = _last_val(term_delta, signal_ts)

        spx_below_200dma = (
            bool(spy_px < spy_dma) if spy_px is not None and spy_dma is not None else None
        )
        vix_gt = bool(vix_val > vix_threshold) if vix_val is not None else None
        credit_widen = bool(credit_d > 0) if credit_d is not None else None
        recession_risk_rising = (
            bool((term_val is not None and term_val < 0) or (term_d is not None and term_d < 0))
            if term_val is not None or term_d is not None
            else None
        )

        bool_flags = [
            f for f in [spx_below_200dma, vix_gt, credit_widen, recession_risk_rising]
            if f is not None
        ]
        risk_off_count = int(sum(1 for f in bool_flags if f)) if bool_flags else None
        high_policy_risk = (
            bool(risk_off_count >= 2) if risk_off_count is not None else None
        )

        rows.append(
            {
                "signal_year": int(year),
                "signal_date": signal_date,
                "spy_px": spy_px,
                "spy_200dma": spy_dma,
                "vix": vix_val,
                "term_spread": term_val,
                "spx_below_200dma": spx_below_200dma,
                "vix_gt_25": vix_gt,
                "credit_spreads_widening": credit_widen,
                "recession_risk_rising": recession_risk_rising,
                "risk_off_count": risk_off_count,
                "high_policy_risk": high_policy_risk,
            }
        )

    return pd.DataFrame(rows)


@st.cache_data
def get_regime_conditioned_acceleration_performance(
    db_path,
    top_n: int = 20,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
):
    bt_df, _ = get_acceleration_event_backtest(
        db_path,
        top_n=top_n,
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
    )
    if bt_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    regime_df = get_policy_risk_regime_features(
        bt_df["signal_year"].tolist(), refresh_token=refresh_token
    )
    if regime_df.empty:
        return bt_df, pd.DataFrame()

    merged = bt_df.merge(regime_df, on="signal_year", how="left")

    checks = [
        ("spx_below_200dma", "SPX < 200DMA"),
        ("vix_gt_25", "VIX > 25"),
        ("credit_spreads_widening", "Credit Spreads Widening"),
        ("recession_risk_rising", "Recession Risk Rising"),
        ("high_policy_risk", "High Policy Risk (2+ flags)"),
    ]
    rows = []
    for col, label in checks:
        if col not in merged.columns:
            continue
        valid = merged[merged[col].notna()].copy()
        if valid.empty:
            continue
        for state in [True, False]:
            sub = valid[valid[col] == state]
            if sub.empty:
                continue
            rows.append(
                {
                    "Regime": label,
                    "State": "True" if state else "False",
                    "Years": int(len(sub)),
                    "Avg Top 3M": round(float(sub["top_return_3m"].dropna().mean()), 2)
                    if sub["top_return_3m"].notna().any()
                    else None,
                    "Avg Top 6M": round(float(sub["top_return_6m"].dropna().mean()), 2)
                    if sub["top_return_6m"].notna().any()
                    else None,
                    "Avg Top 1Y": round(float(sub["top_return_1y"].dropna().mean()), 2)
                    if sub["top_return_1y"].notna().any()
                    else None,
                    "Avg Alpha 1Y (vs Univ)": round(float(sub["alpha_return_1y"].dropna().mean()), 2)
                    if sub["alpha_return_1y"].notna().any()
                    else None,
                    "Avg Alpha 1Y (vs SPX)": round(float(sub["alpha_vs_spx_1y"].dropna().mean()), 2)
                    if sub["alpha_vs_spx_1y"].notna().any()
                    else None,
                }
            )

    return merged, pd.DataFrame(rows)
