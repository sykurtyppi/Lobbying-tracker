"""
Signal returns, S&P benchmark fetches, turnover/cost helpers, benchmark comparisons.
"""

import sqlite3

import pandas as pd
import streamlit as st

import config


@st.cache_data
def get_signal_returns(db_path, year, refresh_token=0):
    """
    Join company_lobbying Q4 with stock_performance to show forward returns
    for top lobbying companies in `year`.

    Uses a ±46-day window around the lag-adjusted Q4 signal date and
    picks the single stored snapshot *closest* to that signal date per ticker via a
    window function.  Does NOT require return_1y so recent years with only
    partial forward data are still included.

    Returns empty DataFrame if stock_performance table has no rows yet.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        # Guard: nothing to join against yet
        chk = pd.read_sql_query(
            "SELECT COUNT(*) AS n FROM stock_performance", conn
        )
        if chk["n"].iloc[0] == 0:
            return pd.DataFrame()

        # Lag-adjusted signal window around Q4 filing availability date.
        ref_date, date_lo, date_hi = _q4_signal_window(int(year), window_days=46)

        df = pd.read_sql_query(
            """
            WITH ranked_sp AS (
                /* For each ticker keep only the snapshot closest to Dec 31 */
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker
                           ORDER BY ABS(JULIANDAY(date) - JULIANDAY(:ref_date))
                       ) AS rn
                FROM stock_performance
                WHERE date BETWEEN :date_lo AND :date_hi
            ),
            nearest_sp AS (
                SELECT * FROM ranked_sp WHERE rn = 1
            ),
            cl_grouped AS (
                /* Aggregate by raw filing entity first */
                SELECT
                    company_name,
                    ticker,
                    sector,
                    SUM(total_lobbying_spend) AS total_spend
                FROM company_lobbying
                WHERE year = :year
                  AND quarter = 'Q4'
                  AND ticker IS NOT NULL
                  AND ticker != ''
                GROUP BY company_name, ticker, sector
                HAVING total_spend >= 1000000
            ),
            cl_ranked AS (
                /* Then collapse to one representative row per ticker */
                SELECT
                    company_name,
                    ticker,
                    sector,
                    total_spend,
                    SUM(total_spend) OVER (PARTITION BY ticker) AS ticker_total_spend,
                    ROW_NUMBER() OVER (
                        PARTITION BY ticker
                        ORDER BY total_spend DESC, company_name ASC
                    ) AS rn
                FROM cl_grouped
            ),
            cl_dedup AS (
                SELECT
                    company_name,
                    ticker,
                    sector,
                    ticker_total_spend AS total_spend
                FROM cl_ranked
                WHERE rn = 1
            )
            SELECT
                cl.company_name,
                cl.ticker,
                cl.sector,
                cl.total_spend,
                sp.date    AS signal_date,
                sp.return_1m,
                sp.return_3m,
                sp.return_6m,
                sp.return_1y
            FROM cl_dedup cl
            JOIN nearest_sp sp ON cl.ticker = sp.ticker
            /* No return_1y filter — include companies even when 1y data
               is not yet available (e.g. current or most-recent year). */
            ORDER BY cl.total_spend DESC
            """,
            conn,
            params={"year": year, "ref_date": ref_date,
                    "date_lo": date_lo, "date_hi": date_hi},
        )
    finally:
        conn.close()

    return df


def _fetch_spx_annual_returns(min_year: int, max_year: int) -> tuple[dict, str | None]:
    """
    Return (spx_dict, error_or_None) where spx_dict maps year → calendar-year
    total return (%), using a fixed hardcoded benchmark table.

    This intentionally avoids live yfinance fetches for benchmark stability
    and reproducibility across runs.
    """
    _SPX_KNOWN_TOTAL_RETURN: dict[int, float] = {
        2019: 31.49,
        2020: 18.40,
        2021: 28.71,
        2022: -18.11,
        2023: 26.29,
        2024: 25.02,
        2025: 17.88,
    }

    spx_dict = {
        yr: _SPX_KNOWN_TOTAL_RETURN[yr]
        for yr in range(min_year, max_year + 1)
        if yr in _SPX_KNOWN_TOTAL_RETURN
    }
    return spx_dict, None


def _sanitize_ticker_list(tickers) -> list[str]:
    """Normalize ticker iterable into sorted unique uppercase symbols."""
    cleaned = {
        str(t).strip().upper()
        for t in (tickers or [])
        if str(t).strip() and str(t).strip().upper() != "NAN"
    }
    return sorted(cleaned)


def _equal_weight_turnover_pct(prev_tickers, curr_tickers) -> float:
    """
    Equal-weight one-period turnover as a percent of portfolio notional.
    100.0 means full portfolio turnover.
    """
    prev = _sanitize_ticker_list(prev_tickers)
    curr = _sanitize_ticker_list(curr_tickers)
    if not prev and not curr:
        return 0.0
    if not prev or not curr:
        return 100.0

    union = set(prev) | set(curr)
    w_prev = 1.0 / len(prev)
    w_curr = 1.0 / len(curr)
    gross_change = 0.0
    for t in union:
        prev_w = w_prev if t in prev else 0.0
        curr_w = w_curr if t in curr else 0.0
        gross_change += abs(curr_w - prev_w)
    return round(0.5 * gross_change * 100.0, 2)


def _cost_pct_from_turnover(turnover_pct, cost_bps: float) -> float:
    """
    Convert turnover% and bps-per-100%-turnover into return percentage points.
    Example: 50% turnover with 10 bps cost => 0.05% return drag.
    """
    if turnover_pct is None or pd.isna(turnover_pct):
        return 0.0
    return round((float(turnover_pct) / 100.0) * (float(cost_bps) / 100.0), 4)


def _q4_signal_window(year: int, window_days: int = 46) -> tuple[str, str, str]:
    """
    Build a lag-adjusted Q4 signal window.
    Returns (ref_date, date_lo, date_hi) as YYYY-MM-DD strings.
    """
    lag_days = max(int(getattr(config, "LDA_FILING_LAG_DAYS", 20) or 0), 0)
    signal_dt = pd.Timestamp(year=int(year), month=12, day=31) + pd.Timedelta(days=lag_days)
    date_lo = (signal_dt - pd.Timedelta(days=window_days)).strftime("%Y-%m-%d")
    date_hi = (signal_dt + pd.Timedelta(days=window_days)).strftime("%Y-%m-%d")
    return signal_dt.strftime("%Y-%m-%d"), date_lo, date_hi


def _fetch_spx_returns_for_signal_years(
    signal_years,
    prefer_forward_window: bool = True,
    window_days: int = 46,
) -> tuple[dict, str | None]:
    """
    Return S&P benchmark returns mapped by hold year (signal_year + 1).

    When possible, computes forward-window returns aligned to the same Q4+lag
    anchor used by strategy returns:
      ref = signal_year Q4 end + filing lag
      fwd = return to ref + 365 days (next available trading day).

    Falls back to hardcoded calendar-year totals when live index history is
    unavailable or incomplete.
    """
    years = sorted(
        {
            int(y)
            for y in (signal_years or [])
            if y is not None and not pd.isna(y)
        }
    )
    if not years:
        return {}, None

    hold_years = [y + 1 for y in years]
    annual_map, _ = _fetch_spx_annual_returns(min(hold_years), max(hold_years))
    if not prefer_forward_window:
        return annual_map, None

    try:
        import yfinance as yf

        first_ref = pd.to_datetime(_q4_signal_window(min(years), window_days=window_days)[0])
        last_ref = pd.to_datetime(_q4_signal_window(max(years), window_days=window_days)[0])
        hist_start = (first_ref - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
        hist_end = (last_ref + pd.Timedelta(days=390)).strftime("%Y-%m-%d")

        hist = yf.Ticker("^GSPC").history(
            start=hist_start,
            end=hist_end,
            auto_adjust=True,
        )
        if hist.empty:
            raise ValueError("empty history for ^GSPC")

        hist = hist.copy()
        hist.index = pd.to_datetime(hist.index)
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)

        def _next_close(target_dt: pd.Timestamp):
            sub = hist.loc[hist.index >= target_dt]
            if sub.empty:
                return None
            return float(sub.iloc[0]["Close"])

        forward_map = {}
        for signal_year in years:
            ref_dt = pd.to_datetime(
                _q4_signal_window(int(signal_year), window_days=window_days)[0]
            )
            end_dt = ref_dt + pd.Timedelta(days=365)
            p0 = _next_close(ref_dt)
            p1 = _next_close(end_dt)
            if p0 is None or p1 is None or p0 <= 0:
                continue
            forward_map[int(signal_year) + 1] = round(((p1 - p0) / p0) * 100.0, 2)

        if not forward_map:
            raise ValueError("unable to derive any forward-window S&P returns")

        merged_map = {}
        fallback_years = []
        missing_years = []
        for hold_year in hold_years:
            if hold_year in forward_map:
                merged_map[int(hold_year)] = float(forward_map[hold_year])
            elif hold_year in annual_map:
                merged_map[int(hold_year)] = float(annual_map[hold_year])
                fallback_years.append(int(hold_year))
            else:
                missing_years.append(int(hold_year))

        msg_parts = []
        if fallback_years:
            msg_parts.append(
                "S&P benchmark mixed mode: forward-window returns used where available; "
                f"calendar fallback for hold year(s): {', '.join(str(y) for y in sorted(fallback_years))}."
            )
        if missing_years:
            msg_parts.append(
                "S&P benchmark missing for hold year(s): "
                + ", ".join(str(y) for y in sorted(missing_years))
                + "."
            )
        return merged_map, (" ".join(msg_parts) if msg_parts else None)
    except Exception as exc:
        if annual_map:
            return (
                annual_map,
                (
                    "S&P forward-window fetch unavailable; using hardcoded "
                    f"calendar-year returns. ({exc})"
                ),
            )
        return {}, f"S&P benchmark unavailable ({exc})"


@st.cache_data
def get_benchmark_comparison(db_path, refresh_token=0):
    """
    Build a year-by-year table comparing the equal-weight lobbying strategy
    return against the S&P 500 return on a hold-year aligned basis.

    Strategy return: equal-weight average 1-year forward return for all companies
    with Q4 lobbying spend >= $1M, measured from a lag-adjusted Q4 signal
    snapshot of `signal_year`. This return is realized during
    `hold_year = signal_year + 1`.
    Benchmark return: S&P 500 return for that same hold year.

    Only years where >= 3 companies have non-null 1y return data are included.
    Returns an empty DataFrame if stock_performance is not yet populated.
    The returned DataFrame includes a '_spx_error' column (str or None) that surfaces
    any yfinance failure so the UI can show a warning instead of silently hiding bars.
    """
    import sqlite3

    cost_bps = float(getattr(config, "BACKTEST_REBALANCE_COST_BPS", 0.0) or 0.0)
    min_return_cov = float(
        getattr(config, "MIN_BACKTEST_RETURN_COVERAGE_PCT", 80.0) or 0.0
    )

    conn = sqlite3.connect(db_path)
    try:
        chk = pd.read_sql_query("SELECT COUNT(*) AS n FROM stock_performance", conn)
        if chk["n"].iloc[0] == 0:
            return pd.DataFrame()

        years_df = pd.read_sql_query(
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
        )

        rows = []
        for year in years_df["year"].tolist():
            ref_date, date_lo, date_hi = _q4_signal_window(int(year), window_days=46)

            df_yr = pd.read_sql_query(
                """
                WITH ranked_sp AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY ticker
                               ORDER BY ABS(JULIANDAY(date) - JULIANDAY(:ref_date))
                           ) AS rn
                    FROM stock_performance
                    WHERE date BETWEEN :date_lo AND :date_hi
                ),
                nearest_sp AS (SELECT * FROM ranked_sp WHERE rn = 1),
                cl_grouped AS (
                    SELECT ticker, SUM(total_lobbying_spend) AS total_spend
                    FROM company_lobbying
                    WHERE year = :year AND quarter = 'Q4'
                      AND ticker IS NOT NULL AND ticker != ''
                    GROUP BY ticker
                    HAVING total_spend >= 1000000
                )
                SELECT
                    cl.ticker,
                    cl.total_spend,
                    sp.return_1y
                FROM cl_grouped cl
                JOIN nearest_sp sp ON cl.ticker = sp.ticker
                """,
                conn,
                params={"year": year, "ref_date": ref_date,
                        "date_lo": date_lo, "date_hi": date_hi},
            )
            if df_yr.empty:
                continue

            valid_df = df_yr[df_yr["return_1y"].notna()].copy()
            mapped_n = int(len(df_yr))
            valid_n = int(len(valid_df))
            return_cov = (valid_n / mapped_n * 100.0) if mapped_n > 0 else 0.0

            if valid_n >= 3 and return_cov >= min_return_cov:
                stats_row = pd.read_sql_query(
                    """
                    WITH q4_entities AS (
                        SELECT
                            CASE
                                WHEN ticker IS NOT NULL AND TRIM(ticker) != ''
                                THEN UPPER(TRIM(ticker))
                                ELSE UPPER(TRIM(company_name))
                            END AS entity_key,
                            SUM(total_lobbying_spend) AS entity_spend,
                            MAX(CASE
                                WHEN ticker IS NOT NULL AND TRIM(ticker) != '' THEN 1
                                ELSE 0
                            END) AS has_ticker
                        FROM company_lobbying
                        WHERE year = :year
                          AND quarter = 'Q4'
                        GROUP BY entity_key
                    )
                    SELECT
                        COUNT(*) AS universe_entities,
                        SUM(entity_spend) AS universe_spend,
                        SUM(CASE WHEN has_ticker = 1 THEN 1 ELSE 0 END) AS mapped_entities,
                        SUM(CASE WHEN has_ticker = 1 THEN entity_spend ELSE 0 END) AS mapped_spend
                    FROM q4_entities
                    WHERE entity_spend >= 1000000
                    """,
                    conn,
                    params={"year": year},
                ).iloc[0]

                signal_year = int(year)
                gross_ret = round(float(valid_df["return_1y"].mean()), 2)
                constituents = _sanitize_ticker_list(valid_df["ticker"].tolist())
                universe_entities = int(stats_row.get("universe_entities") or 0)
                universe_spend = float(stats_row.get("universe_spend") or 0.0)
                included_spend = float(valid_df["total_spend"].sum() or 0.0)

                entity_cov = (
                    (len(constituents) / universe_entities) * 100.0
                    if universe_entities > 0
                    else 0.0
                )
                spend_cov = (
                    (included_spend / universe_spend) * 100.0
                    if universe_spend > 0
                    else 0.0
                )

                rows.append({
                    "signal_year": signal_year,
                    "year": signal_year + 1,  # hold year
                    "strategy_return_gross": gross_ret,
                    "n_companies": valid_n,
                    "mapped_companies": mapped_n,
                    "return_coverage_pct": round(return_cov, 1),
                    "entity_coverage_pct": round(entity_cov, 1),
                    "spend_coverage_pct": round(spend_cov, 1),
                    "constituents": constituents,
                })
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame()

    strat_df = pd.DataFrame(rows).sort_values("signal_year").reset_index(drop=True)
    prev_constituents = []
    turnover_vals = []
    for constituents in strat_df["constituents"].tolist():
        turnover_vals.append(
            _equal_weight_turnover_pct(prev_constituents, constituents)
        )
        prev_constituents = constituents

    strat_df["turnover_pct"] = turnover_vals
    strat_df["cost_applied_pct"] = strat_df["turnover_pct"].apply(
        lambda t: _cost_pct_from_turnover(t, cost_bps)
    )
    strat_df["strategy_return"] = (
        strat_df["strategy_return_gross"] - strat_df["cost_applied_pct"]
    ).round(2)

    spx_dict, spx_error = _fetch_spx_returns_for_signal_years(
        strat_df["signal_year"].astype(int).tolist(),
        prefer_forward_window=True,
    )
    strat_df["spx_return"] = strat_df["year"].map(spx_dict)

    # Suppress error if fallback filled everything
    if spx_error and strat_df["spx_return"].notna().all():
        spx_error = None

    strat_df["_spx_error"] = spx_error
    strat_df = strat_df.drop(columns=["constituents"], errors="ignore")
    return strat_df
