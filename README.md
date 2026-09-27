# Lobbying Tracker

Institutional-style research and dashboard toolkit for U.S. federal lobbying disclosures and equity signal analysis.

[![CI](https://github.com/sykurtyppi/Lobbying-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/sykurtyppi/Lobbying-tracker/actions/workflows/ci.yml)

## What It Does

- Ingests Senate LDA filings and builds normalized company-quarter spend data
- Maps lobbying entities to public tickers with alias + SEC-universe support
- Enriches with market cap and forward return snapshots
- Runs signal research/backtests (acceleration, hist z-spike, sector-neutral, OOS split)
- Serves a Streamlit dashboard for exploration, validation, and production picks

## Project Structure

- `app.py` — Streamlit dashboard
- `src/build_company_lobbying.py` — ingestion + enrichment pipeline
- `src/senate_scraper.py` — Senate API fetcher
- `src/data_fetcher.py` — mapping + utility fetch logic
- `src/analytics_core.py` — non-UI analytics/backtest core
- `src/backtest_signal_research.py` — batch research runner
- `tests/test_regression_integrity.py` — deterministic regression suite
- `config.py` — strategy and pipeline configuration

## Quickstart

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 src/build_company_lobbying.py
venv/bin/streamlit run app.py
```

## Optional Environment Variables

- `LDA_API_KEY` — LDA.gov API key (register at https://lda.gov/api/register/). Raises the fetch rate limit from 15 to 120 requests/minute, cutting a full-year ingest from ~2.5 hours to ~20 minutes.
- `SEC_CONTACT_EMAIL` — contact address sent in the User-Agent for the SEC ticker-universe file. SEC rejects requests without a real address, so the universe sync is skipped until this is set.

```bash
export LDA_API_KEY="..."
export SEC_CONTACT_EMAIL="you@example.com"
```

## Run Tests

```bash
venv/bin/python -m unittest tests.test_regression_integrity -q
```

## Run Research Sweep

```bash
venv/bin/python src/backtest_signal_research.py --db data/lobbying_data.db --out data
```

## Notes

- Default local database path: `data/lobbying_data.db`
- Uses lag-adjusted Q4 signal windows for forward return alignment
- See `documentation/` for workflow and interface details

## Repository Standards

- Contribution guide: `CONTRIBUTING.md`
- Security policy: `SECURITY.md`
- License: `LICENSE`
