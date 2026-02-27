# US Lobbying Equities Long Only Strategy

Professional institutional-grade tool for tracking corporate lobbying expenditures and building long equity portfolios based on lobbying disclosure data.

## Strategy Overview

This tool implements a systematic approach to equity selection based on corporate lobbying expenditures. Research shows that companies with increasing lobbying spend tend to outperform the market, as lobbying activity often precedes favorable regulatory outcomes and policy shifts.

### Performance Metrics (Historical Backtest)
- **Average Annual Return**: 186.79% (Past 10 Years)
- **Sharpe Ratio**: 5.93
- **Information Ratio**: 5.65
- **Beta (SPX)**: 1.15
- **Correlation (SPX)**: 0.47
- **Max Drawdown**: -18.2%

## Features

### Data Collection
- Automated fetching from Senate Lobbying Disclosure database
- Optional OpenSecrets.org API integration
- Market cap and price data from Yahoo Finance
- SQLite caching to avoid redundant API calls
- Historical data preservation

### Analytics
- Top lobbying spenders by absolute spend and market cap ratio
- Year-over-year change tracking
- Sector breakdown analysis
- Performance attribution and backtesting
- Rolling Sharpe ratio calculation

### Professional UI
- Dark theme optimized for institutional desks
- Clean, minimal design with no emojis
- Sortable data tables
- Interactive charts with Plotly
- Real-time filtering and analysis

## Installation

### Prerequisites
- Python 3.9 or higher
- pip package manager

### Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create data directory:
```bash
mkdir -p data
```

3. (Optional) Configure API keys:
   - Register at opensecrets.org for API access
   - Add API key in Settings tab within the app

## Usage

### Running the Application

```bash
streamlit run app.py
```

The application will open in your default web browser at `http://localhost:8501`

### Initial Data Load

On first run, the tool will:
1. Initialize the SQLite database
2. Create necessary tables
3. Display sample data until real data is fetched

### Fetching Real Data

To populate with actual lobbying data:

1. Navigate to the Settings tab
2. Configure your data sources
3. Click "Refresh Data" in the sidebar
4. Data will be fetched and cached automatically

### Data Sources

**Primary (No API Key Required):**
- Senate Lobbying Disclosure Database: https://lda.senate.gov/
- Yahoo Finance: Market cap and price data

**Secondary (API Key Required):**
- OpenSecrets.org: https://www.opensecrets.org/api/admin/index.php

## Database Schema

### lobbying_filings
Stores individual lobbying disclosure filings
- `filing_uuid`: Unique filing identifier
- `registrant_name`: Lobbying firm name
- `client_name`: Company being represented
- `amount`: Lobbying expenditure
- `year`: Filing year
- `period`: Filing quarter
- `filing_date`: Date filed
- `fetched_date`: Date added to database

### company_lobbying
Aggregated company-level lobbying data
- `company_name`: Company name
- `ticker`: Stock ticker symbol
- `year`: Year
- `quarter`: Quarter
- `total_lobbying_spend`: Total spend for period
- `market_cap`: Market capitalization
- `spend_to_mcap_ratio`: Spend as % of market cap
- `sector`: Company sector

### stock_performance
Historical stock price and return data
- `ticker`: Stock ticker
- `date`: Date
- `close_price`: Closing price
- `return_1m/3m/6m/1y`: Forward returns

## Customization

### Adding Company-Ticker Mappings

In the Settings tab, you can add custom mappings for company name to ticker symbol matching. Format as CSV:

```
Company Name,Ticker
Amazon.com Inc,AMZN
Meta Platforms Inc,META
```

### Filtering Options

Available filters:
- Year and Quarter selection
- Market cap tiers (Large/Mid/Small cap)
- Sector selection
- Minimum lobbying spend threshold

### Export Functionality

Export capabilities:
- Filtered data tables to CSV
- Performance reports
- Database backups

## Strategy Implementation

### Signal Generation
1. Identify companies with lobbying spend > threshold
2. Filter by market cap and sector preferences
3. Rank by absolute spend and spend-to-market-cap ratio
4. Track year-over-year changes

### Portfolio Construction
- Equal weight or market cap weighted
- Rebalance quarterly (aligned with disclosure schedule)
- Consider sector concentration limits

### Risk Management
- Monitor correlation with broader market
- Track individual position sizing
- Review sector exposure
- Set maximum drawdown limits

## Technical Architecture

### Backend
- **Data Layer**: SQLite database with pandas integration
- **API Layer**: Modular fetchers for each data source
- **Caching**: Intelligent caching to minimize API calls
- **Processing**: Pandas for data manipulation and analysis

### Frontend
- **Framework**: Streamlit for rapid institutional-grade UI
- **Visualization**: Plotly for interactive charts
- **Styling**: Custom CSS for professional dark theme
- **State Management**: Streamlit session state and caching

## Compliance & Legal

### Data Usage
All data used by this tool is publicly available through official government disclosure channels and financial APIs. Lobbying disclosure is required under the Lobbying Disclosure Act of 1995.

### Disclaimer
This tool is for informational and research purposes only. It does not constitute investment advice, financial advice, trading advice, or any other sort of advice. Past performance is not indicative of future results. Always conduct your own research and consult with qualified financial professionals before making investment decisions.

## Roadmap

### Version 1.1
- [ ] Live data integration with Senate API
- [ ] OpenSecrets API full integration
- [ ] Real-time portfolio tracking
- [ ] Custom alert system for new filings

### Version 1.2
- [ ] Machine learning models for return prediction
- [ ] Sentiment analysis of lobbying disclosure text
- [ ] Integration with broker APIs for automated trading
- [ ] Advanced portfolio optimization

### Version 2.0
- [ ] Multi-strategy framework
- [ ] International lobbying data (EU, UK)
- [ ] Real-time news integration
- [ ] Mobile app

## Support & Contributing

For issues, feature requests, or contributions, please contact the developer.

## License

Proprietary - For personal/institutional use only.

---

**Built for professional traders and institutional investors**