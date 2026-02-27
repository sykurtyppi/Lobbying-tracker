#!/usr/bin/env python3
"""
Research/backtest sweep for lobbying-derived alpha signals.

Outputs:
  - factor_sweep_summary.csv
  - factor_<name>_yearly.csv
  - factor_topn_sensitivity.csv
  - baseline_spend_benchmark.csv
  - best_histz_top10_yearly.csv
  - oos_composite_annual.csv
  - oos_composite_summary.csv
  - oos_composite_weights.csv

Usage:
  venv/bin/python src/backtest_signal_research.py
  venv/bin/python src/backtest_signal_research.py --db data/lobbying_data.db --out data
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from typing import Dict, List

import pandas as pd

# Ensure project root is importable when script is run from src/.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Reuse production analytics logic without importing Streamlit UI runtime.
import config
from src import analytics_core as core


FACTOR_LABELS: Dict[str, str] = {
    "accel_score": "Composite acceleration score",
    "yoy_pct": "YoY lobbying spend %",
    "qoq_pct": "QoQ lobbying spend %",
    "hist_spend_z": "Z-score vs own history",
    "sector_spike_z": "Sector-normalized YoY z-score",
    "mcap_ratio_yoy_pct": "YoY change in spend/market-cap %",
}

_FEATURE_CACHE: Dict[tuple, pd.DataFrame] = {}
_RETURNS_CACHE: Dict[tuple, pd.DataFrame] = {}
_COMPLETE_YEARS_CACHE: Dict[str, List[int]] = {}


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _cached_complete_signal_years(db_path: str) -> List[int]:
    key = str(db_path)
    if key not in _COMPLETE_YEARS_CACHE:
        _COMPLETE_YEARS_CACHE[key] = core._get_complete_signal_years(db_path)
    return _COMPLETE_YEARS_CACHE[key]


def _cached_acceleration_features(
    db_path: str,
    signal_year: int,
    min_spend_m: float,
) -> pd.DataFrame:
    key = (str(db_path), int(signal_year), float(min_spend_m))
    if key not in _FEATURE_CACHE:
        _FEATURE_CACHE[key] = core.get_acceleration_features(
            db_path,
            year=int(signal_year),
            min_spend_m=min_spend_m,
            ticker_only=True,
        )
    return _FEATURE_CACHE[key]


def _cached_signal_returns(db_path: str, signal_year: int) -> pd.DataFrame:
    key = (str(db_path), int(signal_year))
    if key not in _RETURNS_CACHE:
        _RETURNS_CACHE[key] = core.get_signal_returns(db_path, int(signal_year))
    return _RETURNS_CACHE[key]


def _compound_return_pct(series: pd.Series) -> float | None:
    clean = series.dropna().astype(float)
    if clean.empty:
        return None
    gross = 1.0
    for value in clean:
        gross *= 1.0 + value / 100.0
    return (gross - 1.0) * 100.0


def _build_valid_signal_years(db_path: str, min_names_with_1y: int = 10) -> List[int]:
    years = _cached_complete_signal_years(db_path)
    valid: List[int] = []
    for year in years:
        ret_df = _cached_signal_returns(db_path, year)
        if ret_df.empty:
            continue
        if int(ret_df["return_1y"].notna().sum()) >= int(min_names_with_1y):
            valid.append(int(year))
    return valid


def _factor_yearly(
    db_path: str,
    signal_year: int,
    factor_col: str,
    top_n: int,
    min_spend_m: float,
) -> pd.DataFrame:
    feat_df = _cached_acceleration_features(db_path, int(signal_year), min_spend_m)
    ret_df = _cached_signal_returns(db_path, int(signal_year))
    if feat_df.empty or ret_df.empty:
        return pd.DataFrame()

    merged = feat_df.merge(
        ret_df[["ticker", "return_1y", "total_spend"]],
        on="ticker",
        how="inner",
        suffixes=("_feat", "_ret"),
    )
    if merged.empty or factor_col not in merged.columns:
        return pd.DataFrame()

    merged = merged[merged[factor_col].notna()].copy()
    if merged.empty:
        return pd.DataFrame()

    merged = merged.sort_values([factor_col, "annual_spend"], ascending=[False, False])
    top_df = merged.head(int(top_n)).copy()
    top_1y = top_df["return_1y"].dropna()
    if top_1y.empty:
        return pd.DataFrame()

    univ_1y = merged["return_1y"].dropna()
    univ_mean = float(univ_1y.mean()) if not univ_1y.empty else None
    top_mean = float(top_1y.mean())

    return pd.DataFrame(
        [
            {
                "signal_year": int(signal_year),
                "hold_year": int(signal_year) + 1,
                "n_top": int(len(top_1y)),
                "top_return_1y": round(top_mean, 2),
                "universe_return_1y": round(univ_mean, 2) if univ_mean is not None else None,
                "alpha_vs_universe": round(top_mean - univ_mean, 2)
                if univ_mean is not None
                else None,
            }
        ]
    )


def run_research(
    db_path: str,
    out_dir: str,
    top_n: int = 20,
    min_spend_m: float = 1.0,
) -> None:
    warnings.filterwarnings("ignore")
    _ensure_dir(out_dir)

    valid_years = _build_valid_signal_years(db_path, min_names_with_1y=10)
    if not valid_years:
        raise RuntimeError("No signal years have enough matured 1Y forward returns.")

    min_signal_year = int(min(valid_years))
    max_signal_year = int(max(valid_years))
    train_start = int(getattr(config, "OOS_TRAIN_START_YEAR", min_signal_year))
    train_end = int(getattr(config, "OOS_TRAIN_END_YEAR", max_signal_year - 1))
    test_start = int(getattr(config, "OOS_TEST_START_YEAR", train_end + 1))
    test_end = int(getattr(config, "OOS_TEST_END_YEAR", max_signal_year))

    train_start = max(train_start, min_signal_year)
    train_end = min(train_end, max_signal_year)
    if train_end < train_start:
        train_start, train_end = min_signal_year, max_signal_year

    test_start = max(test_start, train_end + 1)
    test_end = min(test_end, max_signal_year)
    if test_start > test_end:
        test_start, test_end = train_end + 1, max_signal_year

    spx_map, _ = core._fetch_spx_returns_for_signal_years(
        valid_years,
        prefer_forward_window=True,
    )

    per_factor: Dict[str, List[dict]] = {k: [] for k in FACTOR_LABELS}
    for signal_year in valid_years:
        for factor_col in FACTOR_LABELS:
            one = _factor_yearly(
                db_path=db_path,
                signal_year=signal_year,
                factor_col=factor_col,
                top_n=top_n,
                min_spend_m=min_spend_m,
            )
            if one.empty:
                continue
            hold_year = int(one.iloc[0]["hold_year"])
            spx_ret = spx_map.get(hold_year)
            one["spx_return"] = spx_ret
            if spx_ret is not None:
                one["alpha_vs_spx"] = (one["top_return_1y"] - float(spx_ret)).round(2)
            else:
                one["alpha_vs_spx"] = None
            per_factor[factor_col].extend(one.to_dict("records"))

    summary_rows = []
    for factor_col, rows in per_factor.items():
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        df = df.sort_values("hold_year").reset_index(drop=True)
        alpha_spx = df["alpha_vs_spx"].dropna()
        alpha_univ = df["alpha_vs_universe"].dropna()
        top_1y = df["top_return_1y"].dropna()
        common = df.dropna(subset=["top_return_1y", "spx_return"])
        cum_top = _compound_return_pct(common["top_return_1y"])
        cum_spx = _compound_return_pct(common["spx_return"])

        summary_rows.append(
            {
                "factor": factor_col,
                "description": FACTOR_LABELS[factor_col],
                "years": int(len(df)),
                "avg_top_1y": round(float(top_1y.mean()), 2) if not top_1y.empty else None,
                "avg_alpha_vs_spx": round(float(alpha_spx.mean()), 2)
                if not alpha_spx.empty
                else None,
                "median_alpha_vs_spx": round(float(alpha_spx.median()), 2)
                if not alpha_spx.empty
                else None,
                "win_rate_vs_spx_%": round(float((df["alpha_vs_spx"] > 0).mean() * 100.0), 1),
                "avg_alpha_vs_universe": round(float(alpha_univ.mean()), 2)
                if not alpha_univ.empty
                else None,
                "win_rate_vs_universe_%": round(
                    float((df["alpha_vs_universe"] > 0).mean() * 100.0), 1
                ),
                "cum_top_1y_%": round(float(cum_top), 2) if cum_top is not None else None,
                "cum_spx_%": round(float(cum_spx), 2) if cum_spx is not None else None,
                "cum_alpha_vs_spx_%": round(float(cum_top - cum_spx), 2)
                if (cum_top is not None and cum_spx is not None)
                else None,
            }
        )

        df.to_csv(os.path.join(out_dir, f"factor_{factor_col}_yearly.csv"), index=False)

    summary_df = pd.DataFrame(summary_rows).sort_values(
        ["avg_alpha_vs_spx", "avg_alpha_vs_universe"], ascending=[False, False]
    )
    summary_df.to_csv(os.path.join(out_dir, "factor_sweep_summary.csv"), index=False)

    # Top-N sensitivity
    sensitivity_rows = []
    for n in [10, 20, 30]:
        for factor_col in FACTOR_LABELS:
            yearly_rows = []
            for signal_year in _cached_complete_signal_years(db_path):
                one = _factor_yearly(
                    db_path=db_path,
                    signal_year=int(signal_year),
                    factor_col=factor_col,
                    top_n=n,
                    min_spend_m=min_spend_m,
                )
                if one.empty:
                    continue
                hold_year = int(one.iloc[0]["hold_year"])
                spx_ret = spx_map.get(hold_year)
                one["spx_return"] = spx_ret
                if spx_ret is not None:
                    one["alpha_vs_spx"] = (one["top_return_1y"] - float(spx_ret)).round(2)
                else:
                    one["alpha_vs_spx"] = None
                yearly_rows.extend(one.to_dict("records"))
            if not yearly_rows:
                continue
            df = pd.DataFrame(yearly_rows).dropna(subset=["alpha_vs_spx"]).copy()
            if df.empty:
                continue
            train = df[
                (df["signal_year"] >= train_start) & (df["signal_year"] <= train_end)
            ]
            test = df[
                (df["signal_year"] >= test_start) & (df["signal_year"] <= test_end)
            ]
            sensitivity_rows.append(
                {
                    "metric": factor_col,
                    "top_n": n,
                    "years": int(len(df)),
                    "avg_alpha_spx": round(float(df["alpha_vs_spx"].mean()), 2),
                    "win_spx_%": round(float((df["alpha_vs_spx"] > 0).mean() * 100.0), 1),
                    "avg_alpha_univ": round(float(df["alpha_vs_universe"].mean()), 2),
                    "train_alpha_spx": round(float(train["alpha_vs_spx"].mean()), 2)
                    if not train.empty
                    else None,
                    "test_alpha_spx": round(float(test["alpha_vs_spx"].mean()), 2)
                    if not test.empty
                    else None,
                    "test_win_spx_%": round(float((test["alpha_vs_spx"] > 0).mean() * 100.0), 1)
                    if not test.empty
                    else None,
                }
            )
    pd.DataFrame(sensitivity_rows).sort_values(
        ["top_n", "avg_alpha_spx"], ascending=[True, False]
    ).to_csv(os.path.join(out_dir, "factor_topn_sensitivity.csv"), index=False)

    # Baseline spend-only benchmark
    bench_df = core.get_benchmark_comparison(db_path)
    if not bench_df.empty:
        b = bench_df.copy()
        b["alpha_vs_spx"] = b.apply(
            lambda r: round(float(r["strategy_return"]) - float(r["spx_return"]), 2)
            if pd.notna(r["strategy_return"]) and pd.notna(r["spx_return"])
            else None,
            axis=1,
        )
        b.to_csv(os.path.join(out_dir, "baseline_spend_benchmark.csv"), index=False)

    # OOS split (composite)
    oos_annual, oos_summary, oos_weights = core.get_acceleration_oos_split_backtest(
        db_path,
        top_n=top_n,
        min_spend_m=min_spend_m,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
    )
    if not oos_annual.empty:
        oos_annual.to_csv(os.path.join(out_dir, "oos_composite_annual.csv"), index=False)
    if not oos_summary.empty:
        oos_summary.to_csv(os.path.join(out_dir, "oos_composite_summary.csv"), index=False)
    if not oos_weights.empty:
        oos_weights.to_csv(os.path.join(out_dir, "oos_composite_weights.csv"), index=False)

    # Reference implementation of best single signal from this run (hist z, top 10)
    best_rows = []
    for signal_year in _cached_complete_signal_years(db_path):
        one = _factor_yearly(
            db_path=db_path,
            signal_year=int(signal_year),
            factor_col="hist_spend_z",
            top_n=10,
            min_spend_m=min_spend_m,
        )
        if one.empty:
            continue
        hold_year = int(one.iloc[0]["hold_year"])
        spx_ret = spx_map.get(hold_year)
        one["spx_return"] = spx_ret
        one["alpha_vs_spx"] = (
            (one["top_return_1y"] - float(spx_ret)).round(2) if spx_ret is not None else None
        )
        best_rows.extend(one.to_dict("records"))
    if best_rows:
        pd.DataFrame(best_rows).sort_values("hold_year").to_csv(
            os.path.join(out_dir, "best_histz_top10_yearly.csv"), index=False
        )

    print("Backtest research completed.")
    print(f"Signal years with matured 1Y windows: {valid_years}")
    print(
        "Configured OOS split used: "
        f"train {train_start}-{train_end}, test {test_start}-{test_end}"
    )
    print(f"Outputs written to: {out_dir}")
    if not summary_df.empty:
        print(summary_df.to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lobbying-signal research backtests.")
    parser.add_argument(
        "--db",
        default="data/lobbying_data.db",
        help="Path to SQLite database (default: data/lobbying_data.db)",
    )
    parser.add_argument(
        "--out",
        default="data",
        help="Output directory for CSV files (default: data)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Top-N names per signal strategy (default: 20)",
    )
    parser.add_argument(
        "--min-spend-m",
        type=float,
        default=1.0,
        help="Minimum annual lobbying spend in $M (default: 1.0)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_research(
        db_path=args.db,
        out_dir=args.out,
        top_n=args.top_n,
        min_spend_m=args.min_spend_m,
    )
