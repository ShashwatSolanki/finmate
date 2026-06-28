"""Yahoo Finance helpers with a browser-like session to avoid empty API responses."""

from __future__ import annotations

import logging
from functools import lru_cache

import yfinance as yf

logger = logging.getLogger(__name__)

# yfinance logs noisy warnings when Yahoo returns non-JSON (rate limits / blocks).
logging.getLogger("yfinance").setLevel(logging.ERROR)


@lru_cache(maxsize=1)
def _yf_session():
    try:
        from curl_cffi import requests as curl_requests

        return curl_requests.Session(impersonate="chrome")
    except ImportError:
        logger.warning("curl_cffi not installed; yfinance may fail intermittently. pip install curl_cffi")
        return None


def get_ticker(symbol: str) -> yf.Ticker:
    session = _yf_session()
    if session is not None:
        return yf.Ticker(symbol, session=session)
    return yf.Ticker(symbol)


def fetch_history(symbol: str, period: str = "3mo"):
    """Return price history DataFrame or None if unavailable."""
    try:
        hist = get_ticker(symbol).history(period=period, auto_adjust=True)
        if hist is not None and not hist.empty:
            return hist
    except Exception as exc:
        logger.debug("history fetch failed for %s: %s", symbol, exc)
    return None


def has_price_series(symbol: str, period: str = "5d") -> bool:
    hist = fetch_history(symbol, period=period)
    return hist is not None and not hist.empty
