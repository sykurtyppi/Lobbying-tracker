"""
Acceleration features, event backtests, production portfolio selection, go/no-go, OOS, sector-neutral.
"""

import math
import sqlite3

import numpy as np
import pandas as pd
import streamlit as st

import config

from dashboard.benchmark import (
    _cost_pct_from_turnover,
    _equal_weight_turnover_pct,
    _sanitize_ticker_list,
    get_signal_returns,
)
from dashboard import benchmark


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL 4 — ACCELERATION / SPIKE FACTOR LAB
# ─────────────────────────────────────────────────────────────────────────────


def _safe_pct_change(curr, prev):
    if pd.isna(curr) or pd.isna(prev) or float(prev) <= 0:
        return None
    return float((float(curr) - float(prev)) / float(prev) * 100.0)


def _to_period_idx(year_val, quarter_val):
    q_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    q_num = q_map.get(str(quarter_val).strip().upper())
    if q_num is None:
        return None
    return int(year_val) * 4 + q_num


@st.cache_data
def get_acceleration_features(
    db_path,
    year: int,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    ticker_only: bool = True,
):
    """
    Build per-entity acceleration features for a given signal year.

    Features implemented:
      - YoY change in annual spend
      - QoQ change in quarterly spend
      - Spend z-score vs entity historical average
      - Sector-normalized spike (z-score of YoY within sector)
      - YoY % change in spend/market-cap ratio
    """
    import sqlite3

    min_hist_year = int(year) - int(getattr(config, "ACCELERATION_LOOKBACK_YEARS", 5))
    min_spend = float(min_spend_m) * 1_000_000.0
    min_feature_count = int(getattr(config, "ACCELERATION_MIN_FEATURE_COUNT", 3))

    conn = sqlite3.connect(db_path)
    try:
        annual_df = pd.read_sql_query(
            """
            WITH base AS (
                SELECT
                    CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE UPPER(TRIM(company_name))
                    END AS entity_key,
                    CASE
                        WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                        ELSE NULL
                    END AS ticker_norm,
                    TRIM(company_name) AS company_name,
                    TRIM(COALESCE(sector, '')) AS sector,
                    year,
                    total_lobbying_spend,
                    market_cap
                FROM company_lobbying
                WHERE year BETWEEN :min_hist_year AND :year
                  AND total_lobbying_spend > 0
            ),
            annual AS (
                SELECT
                    entity_key,
                    year,
                    SUM(total_lobbying_spend) AS annual_spend,
                    MAX(market_cap) AS market_cap
                FROM base
                GROUP BY entity_key, year
            ),
            rep AS (
                SELECT entity_key, year, company_name, sector, ticker_norm
                FROM (
                    SELECT
                        entity_key,
                        year,
                        company_name,
                        sector,
                        ticker_norm,
                        SUM(total_lobbying_spend) AS spend,
                        ROW_NUMBER() OVER (
                            PARTITION BY entity_key, year
                            ORDER BY SUM(total_lobbying_spend) DESC, company_name ASC
                        ) AS rn
                    FROM base
                    GROUP BY entity_key, year, company_name, sector, ticker_norm
                )
                WHERE rn = 1
            )
            SELECT
                a.entity_key,
                a.year,
                a.annual_spend,
                a.market_cap,
                r.company_name,
                NULLIF(r.ticker_norm, '') AS ticker,
                NULLIF(r.sector, '') AS sector
            FROM annual a
            LEFT JOIN rep r
              ON a.entity_key = r.entity_key
             AND a.year = r.year
            ORDER BY a.entity_key, a.year
            """,
            conn,
            params={"min_hist_year": min_hist_year, "year": int(year)},
        )

        quarterly_df = pd.read_sql_query(
            """
            SELECT
                CASE
                    WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN UPPER(TRIM(ticker))
                    ELSE UPPER(TRIM(company_name))
                END AS entity_key,
                year,
                quarter,
                SUM(total_lobbying_spend) AS q_spend
            FROM company_lobbying
            WHERE year BETWEEN :min_hist_year AND :year
              AND total_lobbying_spend > 0
            GROUP BY entity_key, year, quarter
            """,
            conn,
            params={"min_hist_year": min_hist_year, "year": int(year)},
        )
    finally:
        conn.close()

    if annual_df.empty:
        return pd.DataFrame()

    current = annual_df[annual_df["year"] == int(year)].copy()
    if current.empty:
        return pd.DataFrame()

    if ticker_only:
        current = current[current["ticker"].notna() & (current["ticker"].astype(str).str.strip() != "")].copy()
    if current.empty:
        return pd.DataFrame()

    current = current[current["annual_spend"] >= min_spend].copy()
    if current.empty:
        return pd.DataFrame()

    annual_by_entity = {
        ent: grp.sort_values("year").copy()
        for ent, grp in annual_df.groupby("entity_key", dropna=False)
    }
    quarterly_df = quarterly_df.copy()
    quarterly_df["period_idx"] = quarterly_df.apply(
        lambda r: _to_period_idx(r["year"], r["quarter"]), axis=1
    )
    quarterly_df = quarterly_df[quarterly_df["period_idx"].notna()].copy()
    quarterly_by_entity = {
        ent: grp.sort_values("period_idx").copy()
        for ent, grp in quarterly_df.groupby("entity_key", dropna=False)
    }

    rows = []
    for _, row in current.iterrows():
        entity_key = row["entity_key"]
        hist = annual_by_entity.get(entity_key)
        if hist is None or hist.empty:
            continue
        hist = hist.sort_values("year")
        curr_spend = float(row["annual_spend"] or 0.0)
        curr_ratio = (
            (curr_spend / float(row["market_cap"]))
            if pd.notna(row["market_cap"]) and float(row["market_cap"]) > 0
            else None
        )

        prev_row = hist[hist["year"] == int(year) - 1]
        prev_spend = float(prev_row.iloc[0]["annual_spend"]) if not prev_row.empty else None
        prev_ratio = (
            (prev_spend / float(prev_row.iloc[0]["market_cap"]))
            if (
                not prev_row.empty
                and pd.notna(prev_row.iloc[0]["market_cap"])
                and float(prev_row.iloc[0]["market_cap"]) > 0
                and prev_spend is not None
            )
            else None
        )

        yoy_pct = _safe_pct_change(curr_spend, prev_spend)
        mcap_ratio_yoy_pct = _safe_pct_change(curr_ratio, prev_ratio)

        hist_prior = hist[hist["year"] < int(year)]["annual_spend"].dropna().astype(float)
        hist_mean = float(hist_prior.mean()) if not hist_prior.empty else None
        hist_std = float(hist_prior.std(ddof=0)) if len(hist_prior) >= 2 else None
        z_hist = (
            float((curr_spend - hist_mean) / hist_std)
            if hist_mean is not None and hist_std and hist_std > 0
            else None
        )

        q_hist = quarterly_by_entity.get(entity_key)
        qoq_pct = None
        if q_hist is not None and not q_hist.empty:
            q_hist = q_hist[q_hist["period_idx"] <= (int(year) * 4 + 4)].sort_values("period_idx")
            if len(q_hist) >= 2:
                latest_q = float(q_hist.iloc[-1]["q_spend"])
                prior_q = float(q_hist.iloc[-2]["q_spend"])
                qoq_pct = _safe_pct_change(latest_q, prior_q)

        rows.append(
            {
                "entity_key": entity_key,
                "company_name": row.get("company_name"),
                "ticker": row.get("ticker"),
                "sector": row.get("sector"),
                "signal_year": int(year),
                "annual_spend": curr_spend,
                "market_cap": row.get("market_cap"),
                "spend_to_mcap_ratio": curr_ratio,
                "prev_spend": prev_spend,
                "yoy_pct": yoy_pct,
                "qoq_pct": qoq_pct,
                "hist_spend_z": z_hist,
                "mcap_ratio_yoy_pct": mcap_ratio_yoy_pct,
            }
        )

    if not rows:
        return pd.DataFrame()

    feat_df = pd.DataFrame(rows)

    # Industry-normalized spike: z-score of YoY within sector/year.
    sec_stats = (
        feat_df.groupby("sector", dropna=False)["yoy_pct"]
        .agg(["mean", "std"])
        .rename(columns={"mean": "sector_yoy_mean", "std": "sector_yoy_std"})
    )
    feat_df = feat_df.merge(sec_stats, left_on="sector", right_index=True, how="left")
    feat_df["sector_spike_z"] = feat_df.apply(
        lambda r: (
            (r["yoy_pct"] - r["sector_yoy_mean"]) / r["sector_yoy_std"]
            if pd.notna(r["yoy_pct"]) and pd.notna(r["sector_yoy_std"]) and float(r["sector_yoy_std"]) > 0
            else None
        ),
        axis=1,
    )

    score_inputs = {
        "yoy_rank": "yoy_pct",
        "qoq_rank": "qoq_pct",
        "hist_z_rank": "hist_spend_z",
        "sector_spike_rank": "sector_spike_z",
        "mcap_ratio_rank": "mcap_ratio_yoy_pct",
    }
    rank_cols = []
    for out_col, in_col in score_inputs.items():
        if in_col in feat_df.columns:
            ranked = feat_df[in_col].rank(pct=True)
            feat_df[out_col] = (ranked * 100.0).where(feat_df[in_col].notna(), other=None)
            rank_cols.append(out_col)

    feat_df["feature_count"] = feat_df[rank_cols].notna().sum(axis=1)
    feat_df["accel_score"] = feat_df[rank_cols].mean(axis=1, skipna=True)
    feat_df.loc[feat_df["feature_count"] < min_feature_count, "accel_score"] = None

    feat_df = feat_df.sort_values(
        ["accel_score", "annual_spend"], ascending=[False, False]
    ).reset_index(drop=True)
    return feat_df


@st.cache_data
def get_acceleration_event_backtest(
    db_path,
    top_n: int = 20,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
):
    """
    Annual backtest for acceleration factor by event windows (1m/3m/6m/1y).
    Compares top-N acceleration names vs investable ticker universe each year.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        years = pd.read_sql_query(
            """
            SELECT year
            FROM (
                SELECT year, COUNT(DISTINCT quarter) AS n_quarters
                FROM company_lobbying
                GROUP BY year
            )
            WHERE n_quarters = 4
            ORDER BY year
            """,
            conn,
        )["year"].tolist()
    finally:
        conn.close()

    if not years:
        return pd.DataFrame(), pd.DataFrame()

    rows = []
    detail_rows = []
    for signal_year in years:
        feat_df = get_acceleration_features(
            db_path,
            year=int(signal_year),
            refresh_token=refresh_token,
            min_spend_m=min_spend_m,
            ticker_only=True,
        )
        if feat_df.empty:
            continue

        ret_df = get_signal_returns(db_path, int(signal_year), refresh_token=refresh_token)
        if ret_df.empty:
            continue

        merged = feat_df.merge(
            ret_df[
                [
                    "ticker",
                    "return_1m",
                    "return_3m",
                    "return_6m",
                    "return_1y",
                    "total_spend",
                    "signal_date",
                ]
            ],
            on="ticker",
            how="inner",
            suffixes=("_feat", "_ret"),
        )
        if merged.empty:
            continue

        merged = merged.sort_values(["accel_score", "annual_spend"], ascending=[False, False])
        top_df = merged.head(int(top_n)).copy()
        if top_df.empty:
            continue

        hold_year = int(signal_year) + 1
        summary = {
            "signal_year": int(signal_year),
            "year": hold_year,
            "n_universe": int(len(merged)),
            "n_top": int(len(top_df)),
        }
        for col in ["return_1m", "return_3m", "return_6m", "return_1y"]:
            summary[f"top_{col}"] = (
                round(float(top_df[col].dropna().mean()), 2)
                if top_df[col].notna().any()
                else None
            )
            summary[f"universe_{col}"] = (
                round(float(merged[col].dropna().mean()), 2)
                if merged[col].notna().any()
                else None
            )
            if (
                summary[f"top_{col}"] is not None
                and summary[f"universe_{col}"] is not None
            ):
                summary[f"alpha_{col}"] = round(
                    summary[f"top_{col}"] - summary[f"universe_{col}"], 2
                )
            else:
                summary[f"alpha_{col}"] = None

        rows.append(summary)

        keep_cols = [
            "signal_year",
            "ticker",
            "company_name",
            "sector",
            "annual_spend",
            "accel_score",
            "yoy_pct",
            "qoq_pct",
            "hist_spend_z",
            "sector_spike_z",
            "mcap_ratio_yoy_pct",
            "return_1m",
            "return_3m",
            "return_6m",
            "return_1y",
        ]
        top_detail = top_df.copy()
        top_detail["signal_year"] = int(signal_year)
        detail_rows.extend(top_detail[keep_cols].to_dict("records"))

    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    bt_df = pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
    detail_df = (
        pd.DataFrame(detail_rows).sort_values(["signal_year", "accel_score"], ascending=[True, False])
        if detail_rows
        else pd.DataFrame()
    )

    if not bt_df.empty:
        spx_map, spx_err = benchmark._fetch_spx_returns_for_signal_years(
            bt_df["signal_year"].astype(int).tolist(),
            prefer_forward_window=True,
        )
        bt_df["spx_return"] = bt_df["year"].map(spx_map)
        bt_df["alpha_vs_spx_1y"] = bt_df.apply(
            lambda r: round(r["top_return_1y"] - r["spx_return"], 2)
            if pd.notna(r.get("top_return_1y")) and pd.notna(r.get("spx_return"))
            else None,
            axis=1,
        )
        bt_df["_spx_error"] = spx_err

    return bt_df, detail_df


def _get_complete_signal_years(db_path: str) -> list[int]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        years = pd.read_sql_query(
            """
            SELECT year
            FROM (
                SELECT year, COUNT(DISTINCT quarter) AS n_quarters
                FROM company_lobbying
                GROUP BY year
            )
            WHERE n_quarters = 4
            ORDER BY year
            """,
            conn,
        )["year"].tolist()
    finally:
        conn.close()
    return [int(y) for y in years]


def _build_acceleration_year_frame(
    db_path: str,
    signal_year: int,
    refresh_token: int,
    min_spend_m: float,
) -> pd.DataFrame:
    feat_df = get_acceleration_features(
        db_path,
        year=int(signal_year),
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
        ticker_only=True,
    )
    if feat_df.empty:
        return pd.DataFrame()

    ret_df = get_signal_returns(db_path, int(signal_year), refresh_token=refresh_token)
    if ret_df.empty:
        return pd.DataFrame()

    merged = feat_df.merge(
        ret_df[
            [
                "ticker",
                "return_1m",
                "return_3m",
                "return_6m",
                "return_1y",
                "total_spend",
                "signal_date",
            ]
        ],
        on="ticker",
        how="inner",
        suffixes=("_feat", "_ret"),
    )
    if merged.empty:
        return pd.DataFrame()
    merged["signal_year"] = int(signal_year)
    merged["year"] = int(signal_year) + 1
    return merged


def _rank_acceleration_factor(df: pd.DataFrame, factor_col: str, top_n: int) -> pd.DataFrame:
    """Rank a frame by one factor descending and return top-N."""
    if df is None or df.empty or factor_col not in df.columns:
        return pd.DataFrame()
    ranked = df[df[factor_col].notna()].copy()
    if ranked.empty:
        return pd.DataFrame()
    return ranked.sort_values([factor_col, "annual_spend"], ascending=[False, False]).head(int(top_n))


def _select_production_portfolio(
    df: pd.DataFrame,
    top_n: int,
    primary_factor: str,
    fallback_factor: str,
    min_usable_names: int = 5,
    require_return_col: str | None = None,
) -> tuple[pd.DataFrame, str, bool, str | None]:
    """
    Select portfolio by primary factor, falling back to secondary factor if
    primary has insufficient usable names for this year.
    """
    if df is None or df.empty:
        return pd.DataFrame(), primary_factor, False, "No data for this year."

    required = max(1, min(int(top_n), int(min_usable_names)))

    def _usable_count(sel: pd.DataFrame) -> int:
        if sel.empty:
            return 0
        if require_return_col and require_return_col in sel.columns:
            return int(sel[require_return_col].notna().sum())
        return int(len(sel))

    primary_sel = _rank_acceleration_factor(df, primary_factor, top_n)
    primary_usable = _usable_count(primary_sel)
    if primary_usable >= required:
        return primary_sel, primary_factor, False, None

    fallback_sel = _rank_acceleration_factor(df, fallback_factor, top_n)
    fallback_usable = _usable_count(fallback_sel)

    # Prefer fallback if it reaches required coverage when primary does not.
    if fallback_usable >= required:
        return (
            fallback_sel,
            fallback_factor,
            True,
            (
                f"Primary factor `{primary_factor}` had {primary_usable}/{required} usable names; "
                f"used fallback `{fallback_factor}` with {fallback_usable}/{required}."
            ),
        )

    # If neither reaches threshold, keep the richer candidate to avoid empty results.
    if fallback_usable > primary_usable:
        return (
            fallback_sel,
            fallback_factor,
            True,
            (
                f"Both models below required coverage ({required}); "
                f"used fallback `{fallback_factor}` ({fallback_usable} usable) "
                f"over primary `{primary_factor}` ({primary_usable} usable)."
            ),
        )

    if not primary_sel.empty:
        return (
            primary_sel,
            primary_factor,
            False,
            (
                f"Primary factor `{primary_factor}` below required coverage "
                f"({primary_usable}/{required}), fallback not better."
            ),
        )

    if not fallback_sel.empty:
        return (
            fallback_sel,
            fallback_factor,
            True,
            (
                f"Primary factor `{primary_factor}` unavailable; "
                f"used fallback `{fallback_factor}`."
            ),
        )

    return pd.DataFrame(), primary_factor, False, "No eligible ranked names."


@st.cache_data
def get_production_strategy_holdings(
    db_path,
    hold_year: int,
    top_n: int = 10,
    primary_factor: str = "hist_spend_z",
    fallback_factor: str = "accel_score",
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    min_usable_names: int = 5,
):
    """
    Build live holdings for a given hold year using primary/fallback model logic.
    Returns (holdings_df, meta_dict).
    """
    signal_year = int(hold_year) - 1

    feat_df = get_acceleration_features(
        db_path,
        year=signal_year,
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
        ticker_only=True,
    )
    if feat_df.empty:
        return pd.DataFrame(), {
            "hold_year": int(hold_year),
            "signal_year": signal_year,
            "model_used": None,
            "fallback_used": False,
            "reason": "No feature frame available for this signal year.",
        }

    ret_df = get_signal_returns(db_path, signal_year, refresh_token=refresh_token)
    if ret_df.empty:
        frame = feat_df.copy()
        frame["return_1m"] = None
        frame["return_3m"] = None
        frame["return_6m"] = None
        frame["return_1y"] = None
        frame["signal_date"] = None
    else:
        frame = feat_df.merge(
            ret_df[
                [
                    "ticker",
                    "signal_date",
                    "return_1m",
                    "return_3m",
                    "return_6m",
                    "return_1y",
                    "total_spend",
                ]
            ],
            on="ticker",
            how="left",
        )

    selected, model_used, fallback_used, reason = _select_production_portfolio(
        frame,
        top_n=int(top_n),
        primary_factor=str(primary_factor),
        fallback_factor=str(fallback_factor),
        min_usable_names=int(min_usable_names),
        require_return_col=None,
    )
    if selected.empty:
        return pd.DataFrame(), {
            "hold_year": int(hold_year),
            "signal_year": signal_year,
            "model_used": model_used,
            "fallback_used": bool(fallback_used),
            "reason": reason or "No selected names.",
        }

    selected = selected.copy()
    selected["hold_year"] = int(hold_year)
    selected["signal_year"] = signal_year
    selected["model_used"] = model_used

    meta = {
        "hold_year": int(hold_year),
        "signal_year": signal_year,
        "model_used": model_used,
        "fallback_used": bool(fallback_used),
        "reason": reason,
        "selected_n": int(len(selected)),
        "selected_with_1y": int(selected["return_1y"].notna().sum())
        if "return_1y" in selected.columns
        else 0,
    }
    return selected, meta


@st.cache_data
def get_production_strategy_backtest(
    db_path,
    top_n: int = 10,
    primary_factor: str = "hist_spend_z",
    fallback_factor: str = "accel_score",
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    min_usable_names: int = 5,
):
    """
    Annual production-model backtest with primary/fallback factor routing.
    """
    years = _get_complete_signal_years(db_path)
    if not years:
        return pd.DataFrame(), pd.DataFrame()

    rows = []
    detail_rows = []
    for signal_year in years:
        frame = _build_acceleration_year_frame(
            db_path,
            signal_year=int(signal_year),
            refresh_token=refresh_token,
            min_spend_m=min_spend_m,
        )
        if frame.empty:
            continue

        selected, model_used, fallback_used, reason = _select_production_portfolio(
            frame,
            top_n=int(top_n),
            primary_factor=str(primary_factor),
            fallback_factor=str(fallback_factor),
            min_usable_names=int(min_usable_names),
            require_return_col="return_1y",
        )
        if selected.empty:
            continue

        hold_year = int(signal_year) + 1
        top_row = {
            "signal_year": int(signal_year),
            "year": hold_year,
            "model_used": model_used,
            "fallback_used": bool(fallback_used),
            "fallback_reason": reason,
            "n_universe": int(len(frame)),
            "n_selected": int(len(selected)),
        }

        for col in ["return_1m", "return_3m", "return_6m", "return_1y"]:
            sel_ret = selected[col].dropna()
            uni_ret = frame[col].dropna()
            top_row[f"top_{col}"] = round(float(sel_ret.mean()), 2) if not sel_ret.empty else None
            top_row[f"universe_{col}"] = round(float(uni_ret.mean()), 2) if not uni_ret.empty else None
            if top_row[f"top_{col}"] is not None and top_row[f"universe_{col}"] is not None:
                top_row[f"alpha_{col}"] = round(
                    top_row[f"top_{col}"] - top_row[f"universe_{col}"], 2
                )
            else:
                top_row[f"alpha_{col}"] = None
        top_row["return_coverage_pct"] = round(
            100.0 * float(selected["return_1y"].notna().mean()), 1
        )
        rows.append(top_row)

        keep_cols = [
            "signal_year",
            "year",
            "model_used",
            "fallback_used",
            "ticker",
            "company_name",
            "sector",
            "annual_spend",
            "hist_spend_z",
            "accel_score",
            "yoy_pct",
            "qoq_pct",
            "mcap_ratio_yoy_pct",
            "return_1m",
            "return_3m",
            "return_6m",
            "return_1y",
        ]
        tmp = selected.copy()
        tmp["signal_year"] = int(signal_year)
        tmp["year"] = hold_year
        tmp["model_used"] = model_used
        tmp["fallback_used"] = bool(fallback_used)
        detail_rows.extend(tmp[[c for c in keep_cols if c in tmp.columns]].to_dict("records"))

    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    bt_df = pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
    det_df = (
        pd.DataFrame(detail_rows).sort_values(["signal_year", "annual_spend"], ascending=[True, False])
        if detail_rows
        else pd.DataFrame()
    )

    spx_map, spx_err = benchmark._fetch_spx_returns_for_signal_years(
        bt_df["signal_year"].astype(int).tolist(),
        prefer_forward_window=True,
    )
    bt_df["spx_return"] = bt_df["year"].map(spx_map)
    bt_df["alpha_vs_spx_1y"] = bt_df.apply(
        lambda r: round(r["top_return_1y"] - r["spx_return"], 2)
        if pd.notna(r.get("top_return_1y")) and pd.notna(r.get("spx_return"))
        else None,
        axis=1,
    )
    bt_df["_spx_error"] = spx_err
    return bt_df, det_df


@st.cache_data
def get_production_go_nogo_metrics(
    db_path,
    top_n: int = 10,
    primary_factor: str = "hist_spend_z",
    fallback_factor: str = "accel_score",
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    min_usable_names: int = 5,
    sector_k: int = 1,
):
    """
    Compute strict go/no-go diagnostics for production deployment.

    Gates:
      - Net alpha vs S&P 500
      - Outlier robustness (drop best name/year)
      - Sector-neutral robustness
      - Statistical confidence (binomial + bootstrap CI)
    """
    bt_df, det_df = get_production_strategy_backtest(
        db_path,
        top_n=top_n,
        primary_factor=primary_factor,
        fallback_factor=fallback_factor,
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
        min_usable_names=min_usable_names,
    )
    if bt_df.empty:
        return {"ready": False, "reason": "No production backtest rows."}, pd.DataFrame()
    spx_error = (
        bt_df["_spx_error"].dropna().iloc[0]
        if "_spx_error" in bt_df.columns and bt_df["_spx_error"].notna().any()
        else None
    )

    eval_df = bt_df.dropna(subset=["alpha_vs_spx_1y"]).copy()
    if eval_df.empty:
        return {"ready": False, "reason": "No rows with SPX-aligned alpha."}, pd.DataFrame()
    eval_df = eval_df.sort_values("year").reset_index(drop=True)

    # Turnover/cost-adjusted (net) alpha.
    cost_bps = float(getattr(config, "BACKTEST_REBALANCE_COST_BPS", 0.0) or 0.0)
    turnover_rows = []
    prev_constituents = []
    if not det_df.empty and {"year", "ticker"}.issubset(det_df.columns):
        dtmp = det_df.copy()
        dtmp["year"] = pd.to_numeric(dtmp["year"], errors="coerce")
        for year in eval_df["year"].astype(int).tolist():
            curr_constituents = _sanitize_ticker_list(
                dtmp.loc[dtmp["year"] == year, "ticker"].dropna().tolist()
            )
            turnover = _equal_weight_turnover_pct(prev_constituents, curr_constituents)
            cost_pct = _cost_pct_from_turnover(turnover, cost_bps)
            turnover_rows.append(
                {
                    "year": year,
                    "turnover_pct": turnover,
                    "cost_pct": cost_pct,
                }
            )
            prev_constituents = curr_constituents
    if turnover_rows:
        eval_df = eval_df.merge(pd.DataFrame(turnover_rows), on="year", how="left")
    if "cost_pct" not in eval_df.columns:
        eval_df["cost_pct"] = 0.0
    if "turnover_pct" not in eval_df.columns:
        eval_df["turnover_pct"] = np.nan

    eval_df["net_alpha_vs_spx_1y"] = eval_df["alpha_vs_spx_1y"] - eval_df["cost_pct"].fillna(0.0)

    # Outlier robustness: recompute alpha excluding the single best constituent each year.
    ex_best_rows = []
    if not det_df.empty and {"year", "return_1y"}.issubset(det_df.columns):
        d2 = det_df.dropna(subset=["year", "return_1y"]).copy()
        d2["year"] = pd.to_numeric(d2["year"], errors="coerce")
        d2 = d2.dropna(subset=["year"])
        d2["year"] = d2["year"].astype(int)
        for year in eval_df["year"].astype(int).tolist():
            vals = d2.loc[d2["year"] == year, "return_1y"].dropna().astype(float)
            spx_s = eval_df.loc[eval_df["year"] == year, "spx_return"].dropna()
            if len(vals) < 2 or spx_s.empty:
                continue
            ex_best_mean = float(vals.sort_values(ascending=False).iloc[1:].mean())
            ex_best_rows.append(
                {
                    "year": year,
                    "alpha_ex_best_1y": ex_best_mean - float(spx_s.iloc[0]),
                }
            )
    if ex_best_rows:
        eval_df = eval_df.merge(pd.DataFrame(ex_best_rows), on="year", how="left")
    else:
        eval_df["alpha_ex_best_1y"] = np.nan

    # Statistical confidence on raw alpha series.
    alpha_vals = eval_df["alpha_vs_spx_1y"].dropna().astype(float)
    if alpha_vals.empty:
        return {"ready": False, "reason": "Alpha series is empty."}, eval_df
    wins = int((alpha_vals > 0).sum())
    n_obs = int(len(alpha_vals))
    p_binom = 0.0
    for k in range(wins, n_obs + 1):
        p_binom += math.comb(n_obs, k) * (0.5 ** n_obs)

    rng = np.random.default_rng(42)
    boot = rng.choice(alpha_vals.to_numpy(), size=(4000, n_obs), replace=True).mean(axis=1)
    ci_low = float(np.percentile(boot, 2.5))
    ci_high = float(np.percentile(boot, 97.5))

    # Sector-neutral robustness (separate signal family sanity check).
    sn_df = get_sector_neutral_acceleration_backtest(
        db_path,
        top_k_per_sector=int(sector_k),
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
    )
    sn_alpha_vals = pd.Series(dtype=float)
    if not sn_df.empty:
        sn_eval = sn_df.dropna(subset=["signal_year", "year", "ls_return_1y"]).copy()
        if not sn_eval.empty:
            sn_eval["signal_year"] = sn_eval["signal_year"].astype(int)
            sn_map, _ = benchmark._fetch_spx_returns_for_signal_years(
                sn_eval["signal_year"].tolist(),
                prefer_forward_window=True,
            )
            sn_eval["spx_return"] = sn_eval["year"].map(sn_map)
            sn_eval["ls_alpha_vs_spx_1y"] = sn_eval["ls_return_1y"] - sn_eval["spx_return"]
            sn_alpha_vals = sn_eval["ls_alpha_vs_spx_1y"].dropna().astype(float)

    # Gate thresholds
    min_net_alpha = float(getattr(config, "GO_NOGO_MIN_NET_ALPHA_PCT", 2.0))
    min_win_rate = float(getattr(config, "GO_NOGO_MIN_WIN_RATE_PCT", 55.0))
    min_robust_alpha = float(getattr(config, "GO_NOGO_MIN_ROBUST_ALPHA_PCT", 0.0))
    min_sector_alpha = float(getattr(config, "GO_NOGO_MIN_SECTOR_NEUTRAL_ALPHA_PCT", 0.0))
    p_cutoff = float(getattr(config, "GO_NOGO_BINOM_P_CUTOFF", 0.10))
    require_ci_positive = bool(getattr(config, "GO_NOGO_REQUIRE_CI_POSITIVE", True))

    avg_net_alpha = float(eval_df["net_alpha_vs_spx_1y"].dropna().mean())
    net_win_rate = float((eval_df["net_alpha_vs_spx_1y"].dropna() > 0).mean() * 100.0)
    avg_robust_alpha = float(eval_df["alpha_ex_best_1y"].dropna().mean()) if eval_df["alpha_ex_best_1y"].notna().any() else np.nan
    robust_win_rate = float((eval_df["alpha_ex_best_1y"].dropna() > 0).mean() * 100.0) if eval_df["alpha_ex_best_1y"].notna().any() else np.nan
    avg_sector_alpha = float(sn_alpha_vals.mean()) if not sn_alpha_vals.empty else np.nan
    sector_win_rate = float((sn_alpha_vals > 0).mean() * 100.0) if not sn_alpha_vals.empty else np.nan

    pass_net = (avg_net_alpha >= min_net_alpha) and (net_win_rate >= min_win_rate)
    pass_robust = (
        pd.notna(avg_robust_alpha)
        and (avg_robust_alpha >= min_robust_alpha)
        and (pd.notna(robust_win_rate) and robust_win_rate >= 50.0)
    )
    pass_sector = (
        pd.notna(avg_sector_alpha)
        and (avg_sector_alpha >= min_sector_alpha)
        and (pd.notna(sector_win_rate) and sector_win_rate >= 50.0)
    )
    pass_significance = (p_binom <= p_cutoff) and (
        (ci_low > 0.0) if require_ci_positive else True
    )
    go_live = bool(pass_net and pass_robust and pass_sector and pass_significance)

    summary = {
        "ready": True,
        "go_live": go_live,
        "spx_error": spx_error,
        "n_years": n_obs,
        "avg_alpha_raw": float(alpha_vals.mean()),
        "avg_alpha_net": avg_net_alpha,
        "win_rate_raw": float((alpha_vals > 0).mean() * 100.0),
        "win_rate_net": net_win_rate,
        "avg_alpha_ex_best": avg_robust_alpha,
        "win_rate_ex_best": robust_win_rate,
        "avg_turnover_pct": float(eval_df["turnover_pct"].dropna().mean()) if eval_df["turnover_pct"].notna().any() else np.nan,
        "avg_cost_drag_pct": float(eval_df["cost_pct"].dropna().mean()) if eval_df["cost_pct"].notna().any() else np.nan,
        "avg_sector_neutral_alpha": avg_sector_alpha,
        "win_rate_sector_neutral": sector_win_rate,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_binom": float(p_binom),
        "pass_net": pass_net,
        "pass_robust": pass_robust,
        "pass_sector": pass_sector,
        "pass_significance": pass_significance,
        "threshold_net_alpha": min_net_alpha,
        "threshold_win_rate": min_win_rate,
        "threshold_robust_alpha": min_robust_alpha,
        "threshold_sector_alpha": min_sector_alpha,
        "threshold_p": p_cutoff,
    }
    return summary, eval_df


@st.cache_data
def get_acceleration_rolling_windows(
    db_path,
    top_n: int = 20,
    window_years: int = 3,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
):
    """
    Rolling-window stability test for acceleration alpha.
    Example windows for 3 years: 2019–2021, 2020–2022, ...
    """
    win = max(int(window_years), 2)
    bt_df, _ = get_acceleration_event_backtest(
        db_path,
        top_n=top_n,
        refresh_token=refresh_token,
        min_spend_m=min_spend_m,
    )
    if bt_df.empty:
        return pd.DataFrame()

    years = sorted(bt_df["signal_year"].dropna().astype(int).unique().tolist())
    if len(years) < win:
        return pd.DataFrame()

    rows = []
    for i in range(0, len(years) - win + 1):
        y0 = years[i]
        y1 = years[i + win - 1]
        sub = bt_df[(bt_df["signal_year"] >= y0) & (bt_df["signal_year"] <= y1)].copy()
        if sub.empty:
            continue

        rows.append(
            {
                "window_start": y0,
                "window_end": y1,
                "window_label": f"{y0}-{y1}",
                "n_years": int(len(sub)),
                "avg_alpha_3m": round(float(sub["alpha_return_3m"].dropna().mean()), 2)
                if sub["alpha_return_3m"].notna().any()
                else None,
                "avg_alpha_6m": round(float(sub["alpha_return_6m"].dropna().mean()), 2)
                if sub["alpha_return_6m"].notna().any()
                else None,
                "avg_alpha_1y": round(float(sub["alpha_return_1y"].dropna().mean()), 2)
                if sub["alpha_return_1y"].notna().any()
                else None,
                "win_rate_1y": round(
                    float((sub["alpha_return_1y"] > 0).mean() * 100.0), 1
                )
                if sub["alpha_return_1y"].notna().any()
                else None,
            }
        )

    return pd.DataFrame(rows)


@st.cache_data
def get_acceleration_oos_split_backtest(
    db_path,
    top_n: int = 20,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    train_start: int = 2019,
    train_end: int = 2022,
    test_start: int = 2023,
    test_end: int = 2026,
):
    """
    Out-of-sample split:
      - Fit linear factor weights on train years.
      - Apply frozen weights to all years.
      - Report train vs test performance separately.
    """
    import numpy as np
    import warnings

    years = _get_complete_signal_years(db_path)
    years = [y for y in years if y >= int(train_start) and y <= int(test_end)]
    if not years:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    frames = []
    for signal_year in years:
        yr = _build_acceleration_year_frame(
            db_path, signal_year, refresh_token=refresh_token, min_spend_m=min_spend_m
        )
        if not yr.empty:
            frames.append(yr)
    if not frames:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        full_df = pd.concat(frames, ignore_index=True)
    feature_cols = [
        "yoy_rank",
        "qoq_rank",
        "hist_z_rank",
        "sector_spike_rank",
        "mcap_ratio_rank",
    ]
    model_df = full_df[
        full_df["signal_year"].between(int(train_start), int(train_end))
        & full_df["return_1y"].notna()
    ].copy()
    if model_df.empty or len(model_df) < (len(feature_cols) + 10):
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    x_train = model_df[feature_cols].copy().fillna(50.0)
    y_train = model_df["return_1y"].astype(float).values
    mu = x_train.mean()
    sigma = x_train.std(ddof=0).replace(0, 1.0)
    xz = (x_train - mu) / sigma

    ridge = float(getattr(config, "OOS_RIDGE_L2", 1.0))
    x_mat = np.column_stack([np.ones(len(xz)), xz.values])
    eye = np.eye(x_mat.shape[1], dtype=float)
    eye[0, 0] = 0.0  # do not penalize intercept
    beta = np.linalg.pinv(x_mat.T @ x_mat + ridge * eye) @ (x_mat.T @ y_train)

    coef_rows = [{"feature": "intercept", "weight": float(beta[0])}]
    for i, col in enumerate(feature_cols, start=1):
        coef_rows.append({"feature": col, "weight": float(beta[i])})
    coef_df = pd.DataFrame(coef_rows)

    annual_rows = []
    for signal_year in sorted(full_df["signal_year"].dropna().astype(int).unique()):
        yr = full_df[full_df["signal_year"] == int(signal_year)].copy()
        if yr.empty:
            continue

        x_yr = yr[feature_cols].copy().fillna(50.0)
        xz_yr = (x_yr - mu) / sigma
        yr["model_score"] = float(beta[0]) + (xz_yr.values @ beta[1:])
        yr = yr.sort_values(["model_score", "annual_spend"], ascending=[False, False])

        top_df = yr.head(int(top_n)).copy()
        if top_df.empty:
            continue

        split = "Other"
        if int(train_start) <= int(signal_year) <= int(train_end):
            split = "Train"
        elif int(test_start) <= int(signal_year) <= int(test_end):
            split = "Test"

        row = {
            "signal_year": int(signal_year),
            "year": int(signal_year) + 1,
            "split": split,
            "n_universe": int(len(yr)),
            "n_top": int(len(top_df)),
        }
        for col in ["return_3m", "return_6m", "return_1y"]:
            top_ret = top_df[col].dropna()
            univ_ret = yr[col].dropna()
            row[f"top_{col}"] = round(float(top_ret.mean()), 2) if not top_ret.empty else None
            row[f"universe_{col}"] = round(float(univ_ret.mean()), 2) if not univ_ret.empty else None
            if row[f"top_{col}"] is not None and row[f"universe_{col}"] is not None:
                row[f"alpha_{col}"] = round(
                    row[f"top_{col}"] - row[f"universe_{col}"], 2
                )
            else:
                row[f"alpha_{col}"] = None
        annual_rows.append(row)

    annual_df = pd.DataFrame(annual_rows).sort_values("signal_year").reset_index(drop=True)
    if annual_df.empty:
        return pd.DataFrame(), pd.DataFrame(), coef_df

    summary_rows = []
    for split_label in ["Train", "Test"]:
        sub = annual_df[annual_df["split"] == split_label].copy()
        if sub.empty:
            continue
        summary_rows.append(
            {
                "Split": split_label,
                "Years": int(len(sub)),
                "Avg Alpha 3M": round(float(sub["alpha_return_3m"].dropna().mean()), 2)
                if sub["alpha_return_3m"].notna().any()
                else None,
                "Avg Alpha 6M": round(float(sub["alpha_return_6m"].dropna().mean()), 2)
                if sub["alpha_return_6m"].notna().any()
                else None,
                "Avg Alpha 1Y": round(float(sub["alpha_return_1y"].dropna().mean()), 2)
                if sub["alpha_return_1y"].notna().any()
                else None,
                "1Y Win Rate": round(float((sub["alpha_return_1y"] > 0).mean() * 100.0), 1)
                if sub["alpha_return_1y"].notna().any()
                else None,
            }
        )
    summary_df = pd.DataFrame(summary_rows)
    return annual_df, summary_df, coef_df


@st.cache_data
def get_acceleration_simplicity_check(
    db_path,
    top_n: int = 20,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
    simple_metric: str = "yoy_pct",
):
    """
    Compare composite acceleration score vs a single metric (default YoY%).
    """
    years = _get_complete_signal_years(db_path)
    rows = []
    for signal_year in years:
        yr = _build_acceleration_year_frame(
            db_path, signal_year, refresh_token=refresh_token, min_spend_m=min_spend_m
        )
        if yr.empty or simple_metric not in yr.columns:
            continue

        comp_df = yr.sort_values(["accel_score", "annual_spend"], ascending=[False, False]).head(int(top_n))
        simp_df = (
            yr[yr[simple_metric].notna()]
            .sort_values([simple_metric, "annual_spend"], ascending=[False, False])
            .head(int(top_n))
        )
        if comp_df.empty or simp_df.empty:
            continue

        row = {
            "signal_year": int(signal_year),
            "year": int(signal_year) + 1,
            "n_universe": int(len(yr)),
            "n_comp": int(len(comp_df)),
            "n_simple": int(len(simp_df)),
            "overlap_pct": round(
                100.0
                * len(set(comp_df["ticker"].tolist()) & set(simp_df["ticker"].tolist()))
                / max(int(top_n), 1),
                1,
            ),
        }
        for col in ["return_3m", "return_6m", "return_1y"]:
            comp_ret = comp_df[col].dropna()
            simp_ret = simp_df[col].dropna()
            row[f"comp_{col}"] = round(float(comp_ret.mean()), 2) if not comp_ret.empty else None
            row[f"simple_{col}"] = round(float(simp_ret.mean()), 2) if not simp_ret.empty else None
            if row[f"comp_{col}"] is not None and row[f"simple_{col}"] is not None:
                row[f"simple_minus_comp_{col}"] = round(
                    row[f"simple_{col}"] - row[f"comp_{col}"], 2
                )
            else:
                row[f"simple_minus_comp_{col}"] = None
        rows.append(row)

    annual_df = pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
    if annual_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    tol = float(getattr(config, "SIMPLICITY_ALPHA_DIFF_TOL_PCT", 0.5))
    summary = {
        "Avg overlap %": round(float(annual_df["overlap_pct"].dropna().mean()), 1)
        if annual_df["overlap_pct"].notna().any()
        else None,
        "Avg (Simple-Comp) 3M": round(float(annual_df["simple_minus_comp_return_3m"].dropna().mean()), 2)
        if annual_df["simple_minus_comp_return_3m"].notna().any()
        else None,
        "Avg (Simple-Comp) 6M": round(float(annual_df["simple_minus_comp_return_6m"].dropna().mean()), 2)
        if annual_df["simple_minus_comp_return_6m"].notna().any()
        else None,
        "Avg (Simple-Comp) 1Y": round(float(annual_df["simple_minus_comp_return_1y"].dropna().mean()), 2)
        if annual_df["simple_minus_comp_return_1y"].notna().any()
        else None,
        "Simplicity verdict": "Comparable"
        if annual_df["simple_minus_comp_return_1y"].notna().any()
        and abs(float(annual_df["simple_minus_comp_return_1y"].dropna().mean())) <= tol
        else "Composite Adds Value",
        "Tolerance (abs diff 1Y)": tol,
    }

    summary_df = pd.DataFrame([summary])
    return annual_df, summary_df


@st.cache_data
def get_sector_neutral_acceleration_backtest(
    db_path,
    top_k_per_sector: int = 1,
    refresh_token: int = 0,
    min_spend_m: float = 1.0,
):
    """
    Sector-neutral long/short test:
      - Long top-k acceleration names per sector
      - Short bottom-k acceleration names per sector
    """
    import sqlite3

    top_k = max(int(top_k_per_sector), 1)
    conn = sqlite3.connect(db_path)
    try:
        years = pd.read_sql_query(
            """
            SELECT year
            FROM (
                SELECT year, COUNT(DISTINCT quarter) AS n_quarters
                FROM company_lobbying
                GROUP BY year
            )
            WHERE n_quarters = 4
            ORDER BY year
            """,
            conn,
        )["year"].tolist()
    finally:
        conn.close()

    rows = []
    for signal_year in years:
        feat_df = get_acceleration_features(
            db_path,
            year=int(signal_year),
            refresh_token=refresh_token,
            min_spend_m=min_spend_m,
            ticker_only=True,
        )
        if feat_df.empty:
            continue
        ret_df = get_signal_returns(db_path, int(signal_year), refresh_token=refresh_token)
        if ret_df.empty:
            continue

        merged = feat_df.merge(
            ret_df[["ticker", "return_1m", "return_3m", "return_6m", "return_1y"]],
            on="ticker",
            how="inner",
        )
        merged = merged[merged["sector"].notna() & (merged["sector"].astype(str).str.strip() != "")]
        if merged.empty:
            continue

        long_rows = []
        short_rows = []
        sector_count = 0
        for _, sec_df in merged.groupby("sector", dropna=False):
            sec_df = sec_df.sort_values("accel_score", ascending=False)
            if len(sec_df) < (2 * top_k):
                continue
            sector_count += 1
            long_rows.append(sec_df.head(top_k))
            short_rows.append(sec_df.tail(top_k))

        if sector_count == 0:
            continue

        long_df = pd.concat(long_rows, ignore_index=True)
        short_df = pd.concat(short_rows, ignore_index=True)

        row = {
            "signal_year": int(signal_year),
            "year": int(signal_year) + 1,
            "n_sectors": int(sector_count),
            "n_long": int(len(long_df)),
            "n_short": int(len(short_df)),
        }
        for col in ["return_1m", "return_3m", "return_6m", "return_1y"]:
            long_ret = long_df[col].dropna()
            short_ret = short_df[col].dropna()
            row[f"long_{col}"] = round(float(long_ret.mean()), 2) if not long_ret.empty else None
            row[f"short_{col}"] = round(float(short_ret.mean()), 2) if not short_ret.empty else None
            if row[f"long_{col}"] is not None and row[f"short_{col}"] is not None:
                row[f"ls_{col}"] = round(row[f"long_{col}"] - row[f"short_{col}"], 2)
            else:
                row[f"ls_{col}"] = None
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
