"""
Persisted dashboard settings (data/app_settings.json) and custom ticker mapping import.
"""

import os
import json
from datetime import datetime, timezone

import streamlit as st

import config


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_SETTINGS_PATH = os.path.join(_REPO_ROOT, "data", "app_settings.json")


# JSON key → (config attribute name, type coercion)
_SETTINGS_CONFIG_MAP: dict[str, tuple[str, type]] = {
    "senate_api_endpoint":        ("SENATE_API_BASE",              str),
    "senate_auto_update":         ("SENATE_AUTO_UPDATE",            bool),
    "senate_fetch_interval_hours":("SENATE_UPDATE_INTERVAL_HOURS",  int),
    "lda_filing_lag_days":        ("LDA_FILING_LAG_DAYS",           int),
    "sec_universe_auto_sync":     ("SEC_UNIVERSE_AUTO_SYNC",        bool),
    "sec_universe_refresh_days":  ("SEC_UNIVERSE_REFRESH_DAYS",     int),
    "sec_user_agent":             ("SEC_USER_AGENT",                str),
    "opensecrets_api_key":        ("OPENSECRETS_API_KEY",           str),
    "opensecrets_enabled":        ("OPENSECRETS_ENABLED",           bool),
    "yfinance_realtime_mcap":     ("YFINANCE_REALTIME_MCAP_ENABLED", bool),
    "yfinance_price_history":     ("YFINANCE_PRICE_HISTORY_ENABLED",  bool),
    "yfinance_history_years":     ("YFINANCE_HISTORY_YEARS",        int),
}


def _config_defaults() -> dict:
    """Build settings defaults from config.py — config.py is the single source of truth."""
    yf_realtime_enabled = bool(
        getattr(config, "YFINANCE_REALTIME_MCAP_ENABLED", config.YFINANCE_ENABLED)
    )
    yf_price_history_enabled = bool(
        getattr(config, "YFINANCE_PRICE_HISTORY_ENABLED", config.YFINANCE_ENABLED)
    )
    return {
        "senate_api_endpoint":        config.SENATE_API_BASE,
        "senate_auto_update":         config.SENATE_AUTO_UPDATE,
        "senate_fetch_interval_hours": config.SENATE_UPDATE_INTERVAL_HOURS,
        "lda_filing_lag_days":        config.LDA_FILING_LAG_DAYS,
        "sec_universe_auto_sync":     config.SEC_UNIVERSE_AUTO_SYNC,
        "sec_universe_refresh_days":  config.SEC_UNIVERSE_REFRESH_DAYS,
        "sec_user_agent":             config.SEC_USER_AGENT,
        "opensecrets_api_key":        config.OPENSECRETS_API_KEY,
        "opensecrets_enabled":        config.OPENSECRETS_ENABLED,
        "yfinance_realtime_mcap":     yf_realtime_enabled,
        "yfinance_price_history":     yf_price_history_enabled,
        "yfinance_history_years":     config.YFINANCE_HISTORY_YEARS,
        "custom_ticker_mappings_csv": "",
    }


def load_settings() -> dict:
    """Load persisted settings from JSON file, falling back to config.py defaults."""
    defaults = _config_defaults()
    if os.path.exists(_SETTINGS_PATH):
        try:
            with open(_SETTINGS_PATH, "r") as f:
                saved = json.load(f)
            # Forward-compat merge: config.py defaults for any key not yet in file
            merged = dict(defaults)
            merged.update(saved)
            return merged
        except Exception:
            pass
    return defaults


def apply_settings_to_config(settings: dict) -> None:
    """
    Push user-saved settings back into the live config module so every part
    of the app that reads config constants gets the user-overridden values.
    Called at startup (after load_settings) and after every Save.
    """
    for json_key, (cfg_attr, cast) in _SETTINGS_CONFIG_MAP.items():
        if json_key in settings:
            try:
                setattr(config, cfg_attr, cast(settings[json_key]))
            except (TypeError, ValueError):
                pass


def save_settings(settings: dict) -> bool:
    """Persist settings dict to JSON file and apply them to config. Returns True on success."""
    try:
        os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
        apply_settings_to_config(settings)
        return True
    except Exception as e:
        st.error(f"Could not save settings: {e}")
        return False


def apply_custom_ticker_mappings(csv_text: str, db_path: str) -> tuple[int, list[str]]:
    """
    Parse CSV text (Company Name,TICKER) and persist verified aliases to DB.
    Returns (rows_upserted, list_of_errors).
    """
    import csv
    import io
    import sqlite3

    rows = []
    errors = []

    reader = csv.reader(io.StringIO(csv_text.strip()))
    for i, parts in enumerate(reader, start=1):
        if not parts or all(not str(p).strip() for p in parts):
            continue

        first = str(parts[0]).strip()
        if i == 1 and first.lower() in {"company", "company name", "alias", "alias_name"}:
            continue

        if len(parts) < 2:
            errors.append(f"Line {i}: expected 'Company Name,TICKER'")
            continue

        alias_name = str(parts[0]).strip()
        ticker = str(parts[1]).strip().upper()
        canonical_name = str(parts[2]).strip() if len(parts) > 2 and str(parts[2]).strip() else None

        if not alias_name or not ticker:
            errors.append(f"Line {i}: empty company or ticker")
            continue
        rows.append((alias_name, canonical_name, ticker))

    if not rows:
        return 0, errors

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alias_name TEXT UNIQUE,
                canonical_name TEXT,
                ticker TEXT,
                confidence REAL,
                status TEXT DEFAULT 'verified',
                source TEXT,
                notes TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        cursor.executemany(
            """
            INSERT INTO entity_aliases
            (alias_name, canonical_name, ticker, confidence, status, source, notes, created_at, updated_at)
            VALUES (?, ?, ?, 1.0, 'verified', 'manual_ui', 'imported from settings', ?, ?)
            ON CONFLICT(alias_name) DO UPDATE SET
                canonical_name = COALESCE(excluded.canonical_name, entity_aliases.canonical_name),
                ticker = excluded.ticker,
                confidence = 1.0,
                status = 'verified',
                source = 'manual_ui',
                notes = 'imported from settings',
                updated_at = excluded.updated_at
            """,
            [(alias, canonical, ticker, now_iso, now_iso) for alias, canonical, ticker in rows],
        )
        conn.commit()
    finally:
        conn.close()

    return len(rows), errors
