"""
Currency and percentage display helpers.
"""

import pandas as pd


def format_currency(value):
    """Format number as currency"""
    if pd.isna(value):
        return "N/A"
    if value >= 1e9:
        return f"${value/1e9:.2f}B"
    elif value >= 1e6:
        return f"${value/1e6:.2f}M"
    elif value >= 1e3:
        return f"${value/1e3:.2f}K"
    else:
        return f"${value:.0f}"
