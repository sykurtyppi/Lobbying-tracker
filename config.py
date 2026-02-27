"""
Configuration file for US Lobbying Equities Strategy Tool
Customize settings here to match your preferences
"""

# =============================================================================
# DATA SOURCE CONFIGURATION
# =============================================================================

# Senate Lobbying Disclosure Database
# Prefer lda.gov (lda.senate.gov is scheduled to sunset on 2026-06-30).
SENATE_API_BASE = "https://lda.gov/api/v1"
SENATE_AUTO_UPDATE = True
SENATE_UPDATE_INTERVAL_HOURS = 24
# Safety cap for API pagination (25 records/page)
SENATE_MAX_PAGES = 1200
# Keep only these filing families (substring match, case-insensitive).
# Leave empty to disable type filtering.
SENATE_ALLOWED_FILING_TYPES = []
# Keep only latest amendment/version per registrant+client+year+period.
SENATE_KEEP_LATEST_AMENDMENT_ONLY = True
# Safety gate: when True, do not overwrite yearly snapshots unless scraper
# confirms it reached the API's natural end-of-pagination.
STRICT_FETCH_COMPLETENESS = True
# Assumed LDA filing delay from quarter-end to tradable public signal date.
# Used in stock_performance population and backtest reference windows.
# Reference: Senate LDA Guidance Section 6 (LD-2 due within 20 days after quarter-end):
# https://www.senate.gov/legislative/resources/pdf/S1guidance.pdf
LDA_FILING_LAG_DAYS = 20

# SEC ticker universe (official exchange-listed issuer file)
SEC_UNIVERSE_AUTO_SYNC = True
SEC_UNIVERSE_REFRESH_DAYS = 14
SEC_TICKER_UNIVERSE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_USER_AGENT = "LobbyingTracker/1.0 (research@localhost)"
# Restrict SEC universe to liquid US exchanges for safer mapping.
SEC_ALLOWED_EXCHANGES = ["Nasdaq", "NYSE", "NYSE American"]

# OpenSecrets API (requires registration)
OPENSECRETS_API_KEY = ""  # Add your API key here
OPENSECRETS_ENABLED = False

# SEC ticker universe request timeout (seconds)
SEC_REQUEST_TIMEOUT_SECONDS = 45

# Yahoo Finance (for market data)
# Legacy master switch kept for backward compatibility.
YFINANCE_ENABLED = True
# Granular controls used by the Settings UI.
YFINANCE_REALTIME_MCAP_ENABLED = True
YFINANCE_PRICE_HISTORY_ENABLED = True
YFINANCE_HISTORY_YEARS = 5
# Max allowable gap from quarter-end to selected pricing date when
# building stock_performance. Prevents pre-IPO quarter contamination.
STOCK_REF_MAX_GAP_DAYS = 10

# =============================================================================
# DATABASE CONFIGURATION
# =============================================================================

DATABASE_PATH = "data/lobbying_data.db"
CACHE_ENABLED = True
CACHE_EXPIRY_DAYS = 7

# =============================================================================
# STRATEGY PARAMETERS
# =============================================================================

# Market Cap Filters (in billions USD)
LARGE_CAP_THRESHOLD = 10.0
MID_CAP_THRESHOLD = 2.0
SMALL_CAP_THRESHOLD = 0.3

# Lobbying Spend Filters (in millions USD)
MIN_LOBBYING_SPEND = 1.0
MAX_LOBBYING_SPEND = None

# Year-over-Year Change Thresholds
MIN_YOY_GROWTH = -100.0  # Minimum % change (can be negative)
SIGNIFICANT_YOY_GROWTH = 15.0  # Threshold for "significant" growth

# Portfolio Construction
REBALANCE_FREQUENCY = "QUARTERLY"  # MONTHLY, QUARTERLY, ANNUALLY
MAX_POSITIONS = 50
MIN_POSITIONS = 10
POSITION_SIZING = "EQUAL_WEIGHT"  # EQUAL_WEIGHT, MARKET_CAP_WEIGHT, SPEND_WEIGHT

# Sector Concentration Limits (as percentage of portfolio)
MAX_SECTOR_WEIGHT = 30.0
MIN_SECTOR_WEIGHT = 5.0

# =============================================================================
# RISK MANAGEMENT
# =============================================================================

# Maximum position sizes
MAX_POSITION_SIZE = 10.0  # As percentage of portfolio
MIN_POSITION_SIZE = 0.5

# Stop loss and risk parameters
USE_STOP_LOSS = False
STOP_LOSS_PERCENT = -20.0
MAX_PORTFOLIO_DRAWDOWN = -25.0

# =============================================================================
# PERFORMANCE METRICS
# =============================================================================

# Benchmark for comparison
BENCHMARK_TICKER = "^GSPC"  # S&P 500
RISK_FREE_RATE = 0.04  # 4% annual

# Rolling calculation windows (in trading days)
ROLLING_SHARPE_WINDOW = 252  # 1 year
ROLLING_BETA_WINDOW = 252
ROLLING_CORRELATION_WINDOW = 126  # 6 months

# =============================================================================
# UI CONFIGURATION
# =============================================================================

# Theme colors (hex codes)
THEME = {
    'background': '#0e1117',
    'sidebar': '#1a1d24',
    'card': '#2d3748',
    'text_primary': '#ffffff',
    'text_secondary': '#a0a0a0',
    'accent_blue': '#60a5fa',
    'accent_green': '#22c55e',
    'accent_red': '#ef4444',
    'accent_orange': '#f97316',
    'accent_yellow': '#fbbf24'
}

# Chart settings
CHART_HEIGHT = 400
CHART_FONT_SIZE = 12
SHOW_GRID = True

# Table settings
TABLE_PAGE_SIZE = 25
TABLE_SORTABLE = True
TABLE_SEARCHABLE = True

# =============================================================================
# DATA REFRESH SCHEDULE
# =============================================================================

# Automatic data refresh settings
AUTO_REFRESH_ON_STARTUP = True
AUTO_REFRESH_SCHEDULE = {
    'enabled': False,
    'frequency': 'daily',  # daily, weekly, monthly
    'time': '09:00',  # HH:MM format (24-hour)
}

# =============================================================================
# EXPORT SETTINGS
# =============================================================================

EXPORT_FORMATS = ['CSV', 'XLSX', 'JSON']
EXPORT_DIRECTORY = "exports"
INCLUDE_METADATA = True

# =============================================================================
# LOGGING
# =============================================================================

LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR
LOG_FILE = "logs/lobbying_tracker.log"
LOG_ROTATION = "daily"

# =============================================================================
# ADVANCED SETTINGS
# =============================================================================

# API rate limiting
API_RATE_LIMIT = 10  # requests per second
API_RETRY_ATTEMPTS = 3
API_RETRY_DELAY = 5  # seconds

# Data validation
VALIDATE_TICKER_FORMAT = True
VALIDATE_MARKET_CAP = True
REMOVE_OUTLIERS = True
OUTLIER_STD_THRESHOLD = 3

# Company name matching
FUZZY_MATCHING_ENABLED = True
# CompanyMapper enforces a safety floor of 82 (to reduce fuzzy false positives).
# Values below 82 are raised to 82; values above 82 are allowed (stricter matching).
FUZZY_MATCHING_THRESHOLD = 82  # 0-100, higher = more strict

# Performance optimization
USE_MULTIPROCESSING = False
MAX_WORKERS = 4
BATCH_SIZE = 100

# Backtest realism: per-annual-rebalance implementation cost.
# 10 bps = 0.10% deducted from strategy returns.
BACKTEST_REBALANCE_COST_BPS = 10.0
# Require at least this much return-data coverage to include a backtest year.
# Coverage is measured as companies with non-null 1y return / selected companies.
MIN_BACKTEST_RETURN_COVERAGE_PCT = 80.0

# Acceleration signal defaults
ACCELERATION_LOOKBACK_YEARS = 5
ACCELERATION_MIN_FEATURE_COUNT = 3
ACCELERATION_TOP_N_DEFAULT = 20
SECTOR_NEUTRAL_TOP_K_DEFAULT = 1

# Production model defaults (institutional profile)
# Primary signal: spend spike vs own history (strongest in current backtest).
# Fallback signal: composite acceleration score when primary coverage is thin.
PRODUCTION_PRIMARY_FACTOR = "hist_spend_z"
PRODUCTION_FALLBACK_FACTOR = "accel_score"
PRODUCTION_TOP_N = 10
PRODUCTION_MIN_USABLE_NAMES = 5

# Production go/no-go gates (truth panel)
GO_NOGO_MIN_NET_ALPHA_PCT = 2.0
GO_NOGO_MIN_WIN_RATE_PCT = 55.0
GO_NOGO_MIN_ROBUST_ALPHA_PCT = 0.0
GO_NOGO_MIN_SECTOR_NEUTRAL_ALPHA_PCT = 0.0
GO_NOGO_BINOM_P_CUTOFF = 0.10
GO_NOGO_REQUIRE_CI_POSITIVE = True

# Regime filter defaults
REGIME_VIX_THRESHOLD = 25.0
REGIME_CREDIT_LOOKBACK_DAYS = 63

# Out-of-sample split defaults
OOS_TRAIN_START_YEAR = 2019
OOS_TRAIN_END_YEAR = 2022
OOS_TEST_START_YEAR = 2023
OOS_TEST_END_YEAR = 2026
OOS_RIDGE_L2 = 1.0

# Simplicity decision threshold
SIMPLICITY_ALPHA_DIFF_TOL_PCT = 0.5

# =============================================================================
# ALERT SETTINGS
# =============================================================================

ALERTS_ENABLED = False
ALERT_THRESHOLD_NEW_FILINGS = 5  # Alert if company files >5 more than previous quarter
ALERT_THRESHOLD_SPEND_INCREASE = 50  # Alert if spend increases by >50%
ALERT_THRESHOLD_SECTOR_ROTATION = 20  # Alert if sector concentration changes by >20%

# =============================================================================
# CUSTOM COMPANY MAPPINGS
# =============================================================================

# Add custom company name to ticker mappings here
# Format: 'Company Name': 'TICKER'
CUSTOM_TICKER_MAPPINGS = {
    'Amazon.com Inc': 'AMZN',
    'Amazon.com, Inc.': 'AMZN',
    'Meta Platforms Inc': 'META',
    'Meta Platforms, Inc.': 'META',
    'Alphabet Inc': 'GOOGL',
    'Microsoft Corporation': 'MSFT',
    'Microsoft Corp': 'MSFT',
    'Apple Inc': 'AAPL',
    'Apple Inc.': 'AAPL',
    'Tesla Inc': 'TSLA',
    'Tesla, Inc.': 'TSLA',
    'NVIDIA Corporation': 'NVDA',
    'NVIDIA Corp': 'NVDA',
    'JPMorgan Chase & Co': 'JPM',
    'JPMorgan Chase & Co.': 'JPM',
    'Bank of America Corporation': 'BAC',
    'Bank of America Corp': 'BAC',
    'Pfizer Inc': 'PFE',
    'Pfizer Inc.': 'PFE',
    'Johnson & Johnson': 'JNJ',
    'Exxon Mobil Corporation': 'XOM',
    'ExxonMobil': 'XOM',
    'Chevron Corporation': 'CVX',
    'UnitedHealth Group': 'UNH',
    'Visa Inc': 'V',
    'Mastercard Incorporated': 'MA',
}

# =============================================================================
# SECTOR DEFINITIONS
# =============================================================================

SECTOR_KEYWORDS = {
    'Technology': ['tech', 'software', 'computer', 'internet', 'data', 'cloud', 'AI', 'semiconductor'],
    'Healthcare': ['health', 'pharma', 'medical', 'biotech', 'hospital', 'drug'],
    'Financials': ['bank', 'insurance', 'financial', 'capital', 'credit', 'investment'],
    'Energy': ['oil', 'gas', 'energy', 'petroleum', 'renewable', 'solar', 'wind'],
    'Industrials': ['industrial', 'manufacturing', 'aerospace', 'defense', 'machinery'],
    'Consumer': ['retail', 'consumer', 'restaurant', 'hotel', 'entertainment', 'media'],
    'Utilities': ['utility', 'utilities', 'electric', 'water', 'power'],
    'Materials': ['materials', 'mining', 'chemicals', 'metals', 'steel'],
    'Real Estate': ['real estate', 'REIT', 'property', 'development'],
    'Communications': ['telecom', 'communications', 'wireless', 'broadcasting']
}

# =============================================================================
# DATA QUALITY FILTERS
# =============================================================================

# Exclude filings below these thresholds (data quality)
MIN_FILING_AMOUNT = 5000  # $5,000 minimum
EXCLUDE_NULL_AMOUNTS = True
EXCLUDE_FOREIGN_AGENTS = True

# Company filters
MIN_MARKET_CAP_FOR_TRACKING = 100_000_000  # $100M minimum
EXCLUDE_OTCBB = True  # Exclude over-the-counter bulletin board stocks
EXCLUDE_PINK_SHEETS = True
# Optional: keep only rows that can be mapped to a public ticker.
PUBLIC_COMPANY_ONLY = False

# =============================================================================
# NOTES
# =============================================================================

"""
USAGE NOTES:

1. API Keys:
   - OpenSecrets API key can be obtained from https://www.opensecrets.org/api/admin/
   - Senate lobbying data is publicly available and doesn't require authentication

2. Performance:
   - Disable multiprocessing if you encounter issues on Windows
   - Reduce batch size if you have memory constraints
   - Increase cache expiry for faster performance (but less current data)

3. Strategy Customization:
   - Adjust MIN_YOY_GROWTH to filter for companies increasing lobbying spend
   - Modify MAX_SECTOR_WEIGHT to control concentration risk
   - Change REBALANCE_FREQUENCY based on your trading style

4. Data Quality:
   - The tool automatically handles missing data and outliers
   - Custom ticker mappings help improve company-to-stock matching
   - Sector keywords are used for fuzzy classification

5. Updates:
   - Set AUTO_REFRESH_ON_STARTUP = True for always-current data
   - Configure AUTO_REFRESH_SCHEDULE for hands-free operation
   - Manual refresh available via UI sidebar

For detailed documentation, see README.md
For quick start instructions, see QUICKSTART.md
"""
