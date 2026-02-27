import json
import time
import random
import logging
from typing import Optional

import yfinance as yf
from requests.exceptions import HTTPError

logger = logging.getLogger(__name__)

# Simple global rate limit: one Yahoo request per second
MIN_REQUEST_INTERVAL = 1.0
_last_request_ts = 0.0

# Error messages that indicate transient rate-limiting (retry-able).
# "No data found" is intentionally excluded: it usually means the symbol is
# invalid or delisted (permanent), not a transient 429, so retrying wastes
# time and masks the real problem.
_RETRYABLE_FRAGMENTS = (
    "Too Many Requests",
    "rate limit",
    "Expecting value",   # JSON parse failure from empty 429 body
    "JSONDecodeError",
)


def _respect_rate_limit():
    global _last_request_ts
    now = time.time()
    elapsed = now - _last_request_ts
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_ts = time.time()


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is likely a transient Yahoo Finance error."""
    msg = str(exc)
    return any(fragment in msg for fragment in _RETRYABLE_FRAGMENTS)


def fetch_market_cap_with_backoff(
    ticker: str,
    max_retries: int = 5,
    base_sleep: float = 3.0,
    max_sleep: float = 60.0,
) -> Optional[float]:
    """
    Fetch a market cap from Yahoo Finance with exponential backoff.
    Retries on 429s and JSON decode errors (empty responses from rate limiting).
    Returns None after max_retries without raising.
    """

    for attempt in range(1, max_retries + 1):
        try:
            _respect_rate_limit()

            t = yf.Ticker(ticker)
            # fast_info is lighter than t.info and less prone to JSON errors
            info = getattr(t, "fast_info", None)
            if info is not None:
                cap = getattr(info, "market_cap", None)
                if cap:
                    return float(cap)

            # Fallback to full info dict
            full_info = t.info
            cap = full_info.get("marketCap") or full_info.get("market_cap")
            return float(cap) if cap else None

        except HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status == 429:
                sleep_s = min(base_sleep * (2 ** (attempt - 1)), max_sleep)
                sleep_s += random.uniform(0, 2)
                logger.warning(f"429 for {ticker} (attempt {attempt}). Sleeping {sleep_s:.1f}s.")
                time.sleep(sleep_s)
                continue
            logger.error(f"Yahoo HTTP {status} for {ticker}: {e}")
            return None

        except (json.JSONDecodeError, ValueError) as e:
            # Empty or malformed response — almost always a 429 in disguise
            sleep_s = min(base_sleep * (2 ** (attempt - 1)), max_sleep)
            sleep_s += random.uniform(0, 2)
            logger.warning(
                f"JSON error for {ticker} (attempt {attempt}): {e}. "
                f"Likely rate-limited — sleeping {sleep_s:.1f}s."
            )
            time.sleep(sleep_s)
            continue

        except Exception as e:
            if _is_retryable(e):
                sleep_s = min(base_sleep * (2 ** (attempt - 1)), max_sleep)
                sleep_s += random.uniform(0, 2)
                logger.warning(
                    f"Retryable error for {ticker} (attempt {attempt}): {e}. "
                    f"Sleeping {sleep_s:.1f}s."
                )
                time.sleep(sleep_s)
                continue
            logger.error(f"Non-retryable error fetching {ticker}: {e}")
            return None

    logger.warning(f"Failed to get ticker '{ticker}' after {max_retries} retries — skipping.")
    return None
