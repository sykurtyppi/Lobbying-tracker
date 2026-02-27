# Quick Start Guide
## US Lobbying Equities Strategy Tool

### Installation (5 minutes)

1. **Extract the project files** to your preferred location

2. **Open Terminal/Command Prompt** and navigate to the folder:
   ```bash
   cd path/to/lobbying_tracker
   ```

3. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the application**:
   
   **On Mac/Linux:**
   ```bash
   ./launch.sh
   ```
   
   **On Windows:**
   ```bash
   streamlit run app.py
   ```

5. **The application will open in your browser** at `http://localhost:8501`

### First Time Setup

The tool comes pre-configured with sample data to demonstrate functionality. To use real lobbying data:

#### Option 1: Senate Lobbying Database (Recommended - Free)

1. Click the **Settings** tab in the app
2. Under "Senate Lobbying Disclosure Database", enable auto-updates
3. Click **Refresh Data** in the sidebar
4. The tool will fetch and cache data automatically

#### Option 2: OpenSecrets API (Optional - More Comprehensive)

1. Register for a free API key at https://www.opensecrets.org/api/admin/index.php
2. In the **Settings** tab, expand "OpenSecrets API"
3. Enter your API key
4. Enable OpenSecrets data
5. Click **Refresh Data**

### Using the Tool

#### Main Tabs

1. **Overview**: High-level strategy metrics and current holdings
2. **Top Lobbyists**: Ranked companies by lobbying spend
3. **Performance**: Historical performance analysis and charts
4. **Settings**: Data source configuration and database management

#### Key Features

**Filtering:**
- Year and Quarter selection (sidebar)
- Market cap filters (Large/Mid/Small cap)
- Sector filters
- Minimum spend threshold

**Sorting:**
- Click any column header in data tables to sort
- View companies by absolute spend or spend-to-market-cap ratio

**Analysis:**
- Track year-over-year changes in lobbying spend
- Identify sector trends
- Compare performance vs S&P 500
- Calculate risk metrics (Sharpe, Information Ratio, Beta)

### Building Your Initial Database

To populate the database with historical lobbying data, run:

```bash
python src/build_company_lobbying.py
```

This will fetch recent years from the Senate API, enrich matched issuers with market cap data, and write results to `data/lobbying_data.db`.

**Note:** This process can take 10-30 minutes depending on API rate limits and data volume.

### Adding Custom Company-Ticker Mappings

Some companies may not automatically match to their stock tickers. To add custom mappings:

1. Go to **Settings** tab
2. Scroll to "Ticker Mappings"
3. Add mappings in CSV format:
   ```
   Amazon.com Inc,AMZN
   Meta Platforms Inc,META
   Alphabet Inc,GOOGL
   ```
4. Click **Import Mappings**

### Exporting Data

To export filtered data:
1. Apply your desired filters in the sidebar
2. Click **Export Data** in the sidebar
3. Choose CSV or Excel format
4. Data will be saved to `exports/` folder

### Troubleshooting

**Application won't start:**
- Ensure Python 3.9+ is installed: `python --version`
- Reinstall dependencies: `pip install -r requirements.txt --upgrade`

**No data showing:**
- Check internet connection
- Verify data sources are configured in Settings
- Click Refresh Data to fetch latest filings

**Database errors:**
- Delete `data/lobbying_data.db` and restart the app
- The database will be recreated automatically

**Port already in use:**
- Change the port: `streamlit run app.py --server.port 8502`

### Tips for Professional Use

1. **Regular Updates**: Schedule weekly data refreshes to capture new filings
2. **Custom Filters**: Save your preferred filter settings for quick access
3. **Sector Rotation**: Use sector breakdown to identify rotation opportunities
4. **Correlation Analysis**: Monitor correlation with SPX for portfolio construction
5. **Risk Management**: Set alerts for companies with declining lobbying spend

### Strategy Implementation

**Signal Generation:**
1. Rank companies by lobbying spend (absolute and relative to market cap)
2. Filter for YoY growth > 15%
3. Check sector concentration
4. Verify market cap meets your criteria

**Portfolio Construction:**
- **Equal Weight**: Each position gets equal allocation
- **Market Cap Weighted**: Weight by company size
- **Spend Weighted**: Weight by lobbying expenditure

**Rebalancing:**
- Quarterly (aligned with disclosure schedule)
- Review positions after each earnings season
- Adjust for major policy changes

### Getting Help

For questions, issues, or feature requests:
- Review the full README.md for detailed documentation
- Check the Settings tab for configuration options
- Ensure you're using the latest version

### Next Steps

1. Familiarize yourself with the sample data
2. Configure your preferred data sources
3. Fetch real lobbying data
4. Add custom company-ticker mappings
5. Start analyzing and building your strategy

**Good luck with your lobbying-based equity strategy!**
