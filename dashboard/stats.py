"""
Statistical diagnostics: exact binomial test, overfit diagnostics, return risk stats.
"""

import math

import numpy as np
import pandas as pd

import config


def _exact_binomial_pvalue(n_trials: int, n_wins: int) -> float:
    """One-sided exact binomial p-value under H0: p(win)=0.5."""
    if n_trials <= 0:
        return 1.0
    n_wins = max(0, min(n_wins, n_trials))
    total = 2 ** n_trials
    favorable = sum(math.comb(n_trials, k) for k in range(n_wins, n_trials + 1))
    return favorable / total


def _compute_overfit_diagnostics(
    cmp_df: pd.DataFrame,
    strategy_col: str,
    benchmark_col: str = "spx_return",
) -> dict | None:
    """
    Compute lightweight anti-overfitting diagnostics from annual return series.
    Uses a chronological train/test split plus bootstrap CI on average alpha.
    """
    required_cols = {"year", strategy_col, benchmark_col}
    if not required_cols.issubset(set(cmp_df.columns)):
        return None

    work = cmp_df[["year", strategy_col, benchmark_col]].dropna().copy()
    if len(work) < 4:
        return None

    work = work.sort_values("year").reset_index(drop=True)
    work["alpha"] = work[strategy_col] - work[benchmark_col]
    n_total = len(work)

    n_test = max(2, int(round(n_total * 0.35)))
    n_test = min(n_test, n_total - 2)
    if n_test < 1:
        return None

    train = work.iloc[:-n_test]
    test = work.iloc[-n_test:]
    if len(train) < 2 or len(test) < 1:
        return None

    alpha = work["alpha"].astype(float).to_numpy()
    rng = np.random.default_rng(42)
    boot = rng.choice(alpha, size=(4000, len(alpha)), replace=True).mean(axis=1)
    ci_low, ci_high = np.percentile(boot, [2.5, 97.5])

    hit_total = int((work["alpha"] > 0).sum())
    hit_test = int((test["alpha"] > 0).sum())
    p_binom = _exact_binomial_pvalue(n_total, hit_total)

    train_alpha = float(train["alpha"].mean())
    test_alpha = float(test["alpha"].mean())
    alpha_drift = test_alpha - train_alpha
    avg_alpha = float(work["alpha"].mean())

    if train_alpha > 0 and test_alpha > 0 and ci_low > 0 and p_binom < 0.10:
        verdict = "Robust"
    elif test_alpha < 0 or alpha_drift <= -3.0:
        verdict = "Fragile"
    else:
        verdict = "Needs more data"

    return {
        "n_years": n_total,
        "split": f"{int(train['year'].min())}-{int(train['year'].max())} train / "
                 f"{int(test['year'].min())}-{int(test['year'].max())} test",
        "avg_alpha": avg_alpha,
        "train_alpha": train_alpha,
        "test_alpha": test_alpha,
        "alpha_drift": alpha_drift,
        "hit_rate": float((work["alpha"] > 0).mean() * 100),
        "test_hit_rate": float((test["alpha"] > 0).mean() * 100),
        "p_binom": float(p_binom),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "verdict": verdict,
    }


def _compute_return_risk_stats(
    cmp_df: pd.DataFrame,
    strategy_col: str,
    benchmark_col: str = "spx_return",
) -> dict | None:
    """
    Compute annual-return risk diagnostics for a strategy return series.
    Returns None if not enough observations.
    """
    required_cols = {"year", strategy_col, benchmark_col}
    if not required_cols.issubset(set(cmp_df.columns)):
        return None

    work = cmp_df[["year", strategy_col, benchmark_col]].dropna().copy()
    if len(work) < 3:
        return None
    work = work.sort_values("year").reset_index(drop=True)

    strat = work[strategy_col].astype(float) / 100.0
    bench = work[benchmark_col].astype(float) / 100.0
    alpha = strat - bench

    # Geometric growth and drawdown on annual series.
    equity = (1.0 + strat).cumprod()
    years = len(strat)
    if years <= 0 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return None
    cagr = (equity.iloc[-1] ** (1.0 / years)) - 1.0

    peak = equity.cummax()
    drawdown = (equity / peak) - 1.0
    max_drawdown = float(drawdown.min())

    vol = float(strat.std(ddof=1))
    rf = float(getattr(config, "RISK_FREE_RATE", 0.04))
    sharpe = ((float(strat.mean()) - rf) / vol) if vol > 0 else np.nan

    alpha_std = float(alpha.std(ddof=1))
    info_ratio = (float(alpha.mean()) / alpha_std) if alpha_std > 0 else np.nan

    bench_var = float(bench.var(ddof=1))
    if bench_var > 0:
        beta = float(np.cov(strat, bench, ddof=1)[0, 1] / bench_var)
    else:
        beta = np.nan

    return {
        "n_years": years,
        "avg_return": float(strat.mean() * 100),
        "cagr": float(cagr * 100),
        "vol": float(vol * 100),
        "sharpe": float(sharpe) if pd.notna(sharpe) else None,
        "max_drawdown": float(max_drawdown * 100),
        "info_ratio": float(info_ratio) if pd.notna(info_ratio) else None,
        "beta": float(beta) if pd.notna(beta) else None,
    }
