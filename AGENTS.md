# AGENTS.md — Lobbying-tracker

Per-repo facts for the Hermes review agent (adversarial quant reviewer). This file is the
repo-specific layer under the global SOUL.md identity and the shared six-check review skill
(look-ahead bias, point-in-time violations, survivorship, overfitting/multiple-testing,
cost/execution realism, regime cherry-picking). Report findings as a severity-ranked list
citing exact lines — never a prose summary.

> **Audit provenance.** Verified against `main` at commit `e31c614` on 2026-07-16. Package
> versions, line numbers, sample counts, and "currently surviving" observations are
> point-in-time snapshots — re-verify any specific line / number / version against the
> current tree before relying on it. The structural invariants (data providers, risk tier,
> the classes of pitfall) are durable; the exact citations are not.

## Orientation

Factor research on the thesis **"companies that accelerate federal lobbying spend outperform over the
next year."** Long-only, equal-weight, annual rebalance, benchmarked to the S&P 500 (`^GSPC`). ETL from
Senate LDA disclosures → SQLite → factor sweep + an "OOS composite" → a Streamlit dashboard and result
CSVs. **This repo's headline alphas rest on 4–6 annual observations** — multiple-testing and small-sample
overfitting are the whole game.

## 1. Stack & dependencies

- **Python 3**, single-machine, local **SQLite** (`data/lobbying_data.db`, **not committed** — the `data/*.csv`
  results were generated from a DB not in the repo and cannot be independently reproduced from this checkout).
  `pandas==2.2.0`, `numpy==1.26.4`, `yfinance==0.2.36` (prices + market cap), `streamlit==1.54.0`, `plotly`,
  `requests`/`beautifulsoup4`/`lxml` (Senate scraping), `openpyxl`. **No sklearn/statsmodels** — the OOS "model"
  is a hand-rolled ridge via `numpy.linalg.pinv` (`src/analytics_core.py:810-814`).
- **Run**: `launch.sh` → venv → `streamlit run app.py` (localhost:8501). Research sweep:
  `python src/backtest_signal_research.py --db data/lobbying_data.db --out data`. Tests:
  `python -m unittest tests.test_regression_integrity`.
- `app.py` is ~376 KB / ~7000 lines — it re-declares/wraps the analytics functions with Streamlit cache
  args; tests import from `app`, not `analytics_core`.

## 2. Risk tier — Research/backtesting

No live capital, no broker, no order routing found — output is a dashboard + CSVs (grep-based; strong evidence, not exhaustive proof). It also carries a
**macro/point-in-time** dimension: lobbying is disclosed quarterly with a statutory lag, so date alignment vs
returns is a look-ahead surface. Primary risks, ranked: **(1) severe multiple-testing on a tiny sample,
(2) a weak 2-year "OOS" split, (3) composite-weight overfitting, (4) survivorship, (5) disclosure-lag /
point-in-time.** For any result, ask whether it survives out-of-sample on more than a couple of years. All
factor/backtest/composite findings are **flag-only**.

## 3. Known pitfalls specific to this repo (verified from code)

1. **Multiple-testing on ~4–6 observations.** Only 4–6 signal years exist (`data/factor_sweep_summary.csv`
   years = 4,5,5,5,6). The sweep tests **6 factors × 3 top-N = 18 configs** (`factor_topn_sensitivity.csv`),
   plus the composite, plus a "best single signal" re-export. `config.PRODUCTION_PRIMARY_FACTOR = "hist_spend_z"`
   with the comment "strongest in current backtest" (`config.py:215-216`) proves **the production factor was
   selected as the sweep winner** — textbook in-sample selection. Reported "100.0% win rate"
   (`factor_topn_sensitivity.csv`) is over n=4 years.
2. **The "OOS" split is statistically meaningless.** `analytics_core.get_acceleration_oos_split_backtest`
   freezes weights on Train (2019-2022) and reports Test (2023-2024) = **2 test years**
   (`oos_composite_summary.csv`: Test Years=2, win rate 50%). Train avg 1Y alpha is **negative (−1.32)** while
   Test is +11.13, driven by a single year (2023→2024 alpha +22.47). Genuine walk-forward in *structure*,
   noise in *sample*; the split boundary is itself a tunable config constant (`OOS_TRAIN_END_YEAR=2022`).
3. **Composite weight overfitting.** `oos_composite_weights.csv` shows large opposing weights (`yoy_rank +16.9`,
   `mcap_ratio_rank −13.2`, `sector_spike_rank −7.4`) from a 5-feature ridge on a handful of years; signs flip
   vs the single-factor sweep (where those factors were positive-alpha) — fitting to noise.
4. **Point-in-time market cap is look-ahead AND degenerate.** `build_company_lobbying.get_or_fetch_ticker_info`
   (`:58`) caches one **current** market cap per ticker and writes it onto **every year's** row. Consequences:
   (a) `spend_to_mcap_ratio`/`mcap_ratio_yoy_pct` for 2019-2024 use a **2026 market cap** (forward-looking);
   (b) since prev- and curr-year mcap are identical, `mcap_ratio_yoy_pct` algebraically **collapses to
   `yoy_pct`** (`analytics_core.py:644-657`) — the mcap "factor" is not independent in the sweep.
5. **Disclosure-lag / amendment leakage.** `LDA_FILING_LAG_DAYS = 20` (`config.py:29`) is applied uniformly;
   entry is anchored ~Jan-20 of Y+1, which is defensible for *timing*. But `SENATE_KEEP_LATEST_AMENDMENT_ONLY =
   True` means the **latest amended** Q4 figure is used — an amendment filed months later would not have been
   knowable at entry, so the signal *magnitude* can incorporate post-entry revisions = look-ahead on value.
6. **Survivorship inflates every return series.** `populate_stock_performance` (`build_company_lobbying.py:1433`)
   does `if hist.empty: errors += 1; continue` — delisted/renamed tickers never enter `stock_performance`, so
   both the strategy top-N and the "universe" benchmark are survivors only. `market_data_yahoo._RETRYABLE_FRAGMENTS`
   deliberately excludes "No data found" — delisted symbols are permanently skipped by design.
7. **Benchmark can silently switch definitions.** `_fetch_spx_returns_for_signal_years` prefers a live
   forward-window `^GSPC` return but **falls back to a hardcoded 7-value calendar-year table**
   (`analytics_core.py:72-80`) on any exception; the two are not the same return, so a networkless run changes
   alpha by the Jan-1→Jan-20 drift.
8. **Cost model is thin/optimistic.** `BACKTEST_REBALANCE_COST_BPS = 10.0` applied as `turnover% × 10bps`
   (`_cost_pct_from_turnover`, `:51`) — 10 bps one-way, annual rebalance, no spread/slippage/impact/borrow;
   net vs gross differs ~0.1%.
9. **Entry-price tolerance is ±46 days.** `get_signal_returns` picks the closest `stock_performance` row within
   a ±46-day window around ref_date (`analytics_core.py:205-273`); names can effectively enter up to ~46 days
   early/late, which can straddle year boundaries.
10. **The test suite does NOT guard economic correctness.** `tests/test_regression_integrity.py` runs on a
    **synthetic fixture DB** and asserts only plumbing (`signal_year + 1 == year`, cost arithmetic, dedupe,
    `accel_score ∈ [0,100]`, fuzzy floor). SPX is mocked to zero. **No test checks look-ahead, survivorship,
    PIT market cap, or that any reported alpha is real** — green CI says nothing about validity.
11. **Scraper brittleness.** `senate_scraper.py` hardcodes API base `https://lda.gov/api/v1` (fallback
    `lda.senate.gov`, sunset 2026-06-30), spoofed desktop User-Agent, 25/page capped at 1200 pages; a partial
    fetch that looks complete could silently truncate a year's universe.

## 4. What the agent may fix directly vs only flag

**Default posture is read-only.** During a review-only task, report proposed changes as
findings and do not edit; post inline PR comments only as the configured review bot or when
explicitly asked, not merely because a PR exists. Fixes apply only when explicitly
authorized — and even then, numerical / signal / statistical changes require focused
before/after validation and human review, never a silent edit. "Low-risk" is not risk-free:
UI, scheduler, deploy, CORS, and DB code can still be consequential — treat every item below
as a candidate, not standing authorization.


**Flag only — never auto-fix (numerical result logic; changes move the alpha):**
`src/analytics_core.py` entirely (factor definitions, `_q4_signal_window`, ridge composite, SPX benchmark
selection), `src/backtest_signal_research.py` (the sweep design — this *is* the multiple-testing surface),
`app.py:4422+` `get_production_go_nogo_metrics`, the production-factor selection and `OOS_*` split years and
`BACKTEST_REBALANCE_COST_BPS` in `config.py`, and all `data/*.csv` results.

**Flag (looks like ETL/infra but is numerically load-bearing):**
`build_company_lobbying.populate_stock_performance` (forward-return anchoring, delisted drop, `auto_adjust`),
`get_or_fetch_ticker_info` market-cap caching (the PIT/degeneracy bug), and
`LDA_FILING_LAG_DAYS`/`SENATE_KEEP_LATEST_AMENDMENT_ONLY` handling (amendment leakage).

**Low-risk — only if a fix is explicitly requested, (pure infra/UI/robustness, no effect on reported numbers):**
`src/senate_scraper.py` transport (retries, API-base fallback, User-Agent), `src/market_data_yahoo.py` backoff,
`src/export_unmatched_companies.py`, Streamlit rendering/theming in `app.py`, `launch.sh`, logging —
**provided the set of rows ingested and dates chosen are unchanged.** A scraper change that alters which
filings/years are retained crosses back into flag territory.

## 5. PR etiquette

Findings as **inline review comments on exact lines**, severity-ranked, each naming the concrete failure
(in-sample sweep winner, 2-year OOS, redundant mcap factor, survivorship, amendment leakage). No full-file
rewrites unless explicitly asked to push a fix commit. For flag-only areas, comment and stop. When a "result"
CSV is cited as evidence, note that the source DB is absent from the repo and the number is not reproducible
from this checkout.
