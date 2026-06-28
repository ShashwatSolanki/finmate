"""Ticker extraction — case-sensitive caps, company names, $tags; optional Yahoo validation."""

from __future__ import annotations

import re

_TICKER_DOLLAR = re.compile(r"\$([A-Za-z]{1,5})\b")
# Only tokens that are ALREADY uppercase in the user message (not raw.upper()).
_TICKER_CAPS = re.compile(r"\b([A-Z]{2,5})\b")

_STOP = frozenset(
    {
        "I", "A", "OK", "USD", "INR", "GBP", "EUR", "THE", "AND", "FOR", "ETF", "IPO", "YTD", "OTC",
        "WHERE", "WHAT", "WHEN", "WHICH", "WHO", "WHOM", "WHOSE", "WHY", "HOW",
        "DO", "DOES", "DID", "DONE", "ARE", "WAS", "WERE", "BEEN", "BEING",
        "MY", "ME", "WE", "HE", "IT", "IS", "AM", "AS", "AT", "BY", "IF", "IN",
        "NO", "OF", "ON", "OR", "SO", "TO", "UP", "GO", "AN", "US", "VS",
        "CAN", "MAY", "NOT", "BUT", "ALL", "ANY", "OUT", "NEW", "NOW", "SEE", "BUY",
        "SELL", "INTO", "PER", "GET", "USE", "PAY",
        "WAY", "YES", "YET", "HAD", "HAS", "HER", "HIM", "HIS", "ITS",
        "LET", "MAN", "MEN", "ONE", "OUR", "OWN", "SAY", "SHE", "TOO", "TWO",
        "SPEND", "SAVE", "SPENT", "MAKE", "NEED", "WANT", "HELP", "CASH", "BANK",
        "LOAN", "RENT", "FOOD", "YEAR", "WEEK", "DAYS", "TIME", "WORK", "HOME",
        "LIFE", "PLAN", "GOAL", "MUCH", "MANY", "SOME", "LIKE", "JUST", "ONLY",
        "ALSO", "VERY", "EVEN", "FROM", "WITH", "HAVE", "THAN", "THEN",
        "THAT", "THIS", "THEY", "THEM", "WILL", "WOULD", "COULD", "SHOULD",
        "MIGHT", "MUST", "YOUR", "ABLE", "BACK", "CAME", "COME", "EACH", "ELSE",
        "GIVE", "KEEP", "KNOW", "LAST", "LEFT", "LONG", "LOOK", "MADE",
        "MOST", "MOVE", "OPEN", "OVER", "PART", "RISK", "SAFE",
        "SAME", "SEEM", "SHOW", "SUCH", "SURE", "TAKE", "TELL", "LEVEL",
        "TOLD", "TURN", "USED", "WAYS", "WELL",
        "WENT", "BASE", "CASE", "DATA", "FACT", "FORM", "FULL",
        "HALF", "HIGH", "HOLD", "INFO", "KIND", "LINE", "MEAN", "NEXT",
        "REAL", "SIDE", "TRUE", "TYPE", "UNIT",
        "AREA", "AWAY", "BEST", "CALL", "HERE", "IDEA", "LATE", "WORD",
        # Common false positives from natural language
        "RIGHT", "LEFT", "GOOD", "BAD", "BIG", "LOW", "TOP", "END", "SET",
        "PUT", "RUN", "ADD", "ASK", "TRY", "DAY", "LOT", "BIT", "FEW",
        "TAX", "FEE", "NET", "GROSS", "SUM", "AVG", "MAX", "MIN",
        "SIP", "EMI", "APR", "ROI", "ROE", "EPS", "PE", "PB",
        "BULL", "BEAR", "LONG", "SHORT", "HOLD", "WAIT", "DROP", "RISE",
        "TERM", "NEAR", "FAR", "MORE", "LESS", "EACH", "BOTH", "EITHER",
    }
)

_COMPANY_TO_TICKER = {
    "microsoft": "MSFT",
    "apple": "AAPL",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "nvidia": "NVDA",
    "netflix": "NFLX",
    "amd": "AMD",
    "intel": "INTC",
}

_KNOWN_TICKERS = frozenset(_COMPANY_TO_TICKER.values()) | frozenset(
    {"NFLX", "AMD", "INTC", "SPY", "QQQ", "VOO", "VTI"}
)

_INVESTMENT_SIGNAL = re.compile(
    r"\b("
    r"stock|stocks|ticker|tickers|share|shares|portfolio|invest|investing|investment|"
    r"equity|equities|nasdaq|nyse|nse|bse|sensex|nifty|dividend|market|markets|"
    r"mutual fund|index fund|etf|sip|allocate|allocation|aapl|msft|tsla|googl|amzn|nvda|meta"
    r")\b",
    re.I,
)


def has_investment_signal(message: str) -> bool:
    return bool(_INVESTMENT_SIGNAL.search(message))


def extract_ticker_candidates(message: str, *, max_candidates: int = 6) -> list[str]:
    """Fast local extraction — no network calls. For routing only."""
    raw = message.strip()
    if not raw:
        return []

    lower = raw.lower()
    explicit = [m.group(1).upper() for m in _TICKER_DOLLAR.finditer(raw)]

    name_hits: list[str] = []
    company_words_upper: set[str] = set()
    for name, symbol in _COMPANY_TO_TICKER.items():
        if re.search(rf"\b{re.escape(name)}\b", lower):
            name_hits.append(symbol)
            company_words_upper.add(name.upper())

    dynamic_stop = _STOP | company_words_upper
    caps = [t for t in _TICKER_CAPS.findall(raw) if t not in dynamic_stop]

    ordered: list[str] = []
    seen: set[str] = set()
    for t in explicit + name_hits + caps:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
        if len(ordered) >= max_candidates:
            break
    return ordered


def pick_validated_tickers(message: str, *, max_tickers: int = 3) -> list[str]:
    """Return tickers confirmed by Yahoo price history (silent on failures)."""
    from app.services.market_data import has_price_series

    out: list[str] = []
    for symbol in extract_ticker_candidates(message):
        if len(out) >= max_tickers:
            break
        if has_price_series(symbol):
            out.append(symbol)
    return out
