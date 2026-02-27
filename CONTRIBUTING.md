# Contributing

Thanks for contributing. This project emphasizes data correctness, reproducibility, and clear research methodology.

## Development Setup

```bash
cd "/Users/tristanalejandro/Lobbying tracker"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run Checks Before Opening a PR

```bash
venv/bin/python -m unittest tests.test_regression_integrity -q
venv/bin/python -m py_compile app.py src/*.py tests/*.py
```

## Branch and Commit Guidance

- Create focused branches from `main`
- Keep commits small and descriptive
- Use imperative commit messages (example: `Fix quarterly normalization for Q4Y filings`)

## Pull Request Checklist

- Tests pass locally
- Any schema/logic change includes regression coverage
- Dashboard behavior changes are validated in Streamlit
- Docs/config updates are included when relevant

## Data and Privacy

- Do not commit local databases (`*.db`) or sensitive local artifacts
- Avoid embedding secrets or personal credentials in code/config
