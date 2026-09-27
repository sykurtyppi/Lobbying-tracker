"""
Streamlit page configuration and dashboard CSS theme.
"""

import streamlit as st


_APP_CSS = """
    <style>
    /* Main background */
    .stApp {
        background-color: #0e1117;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #1a1d24;
    }

    /* Metrics */
    [data-testid="stMetricValue"] {
        color: #ffffff;
        font-size: 28px;
        font-weight: 600;
    }

    [data-testid="stMetricLabel"] {
        color: #a0a0a0;
        font-size: 14px;
    }

    /* Headers */
    h1 {
        color: #ffffff;
        font-weight: 700;
        padding-bottom: 10px;
        border-bottom: 2px solid #2d3748;
    }

    h2, h3 {
        color: #e0e0e0;
        font-weight: 600;
    }

    /* Tables */
    .dataframe {
        font-size: 13px;
    }

    .dataframe thead tr th {
        background-color: #1a1d24 !important;
        color: #ffffff !important;
        font-weight: 600;
    }

    .dataframe tbody tr:hover {
        background-color: #2d3748 !important;
    }

    /* Buttons */
    .stButton > button {
        background-color: #2d3748;
        color: #ffffff;
        border: 1px solid #4a5568;
        font-weight: 500;
    }

    .stButton > button:hover {
        background-color: #4a5568;
        border-color: #718096;
    }

    /* Remove default padding */
    .block-container {
        padding-top: 2rem;
        padding-bottom: 0rem;
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }

    .stTabs [data-baseweb="tab"] {
        background-color: #1a1d24;
        color: #a0a0a0;
        border-radius: 4px 4px 0 0;
        padding: 10px 20px;
        font-weight: 500;
    }

    .stTabs [aria-selected="true"] {
        background-color: #2d3748;
        color: #ffffff;
    }

    /* Expander */
    .streamlit-expanderHeader {
        background-color: #1a1d24;
        color: #ffffff;
        font-weight: 500;
    }
    </style>
"""


def _configure_streamlit_page() -> None:
    """Apply page config and dashboard CSS only during UI runtime."""
    st.set_page_config(
        page_title="US Lobbying Equities Strategy",
        page_icon="",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(_APP_CSS, unsafe_allow_html=True)
