"""Investment Analyser — Yahoo Finance via yfinance (quotes, history, SMA) + FinMate NL."""

import re
from decimal import Decimal
from uuid import UUID

import yfinance as yf
from sqlalchemy.orm import Session

from app.agents.types import AgentName, AgentResult
from app.ml.finmate import SYSTEM_EXTRA_INVESTMENT, ensure_investment_reply_shape, generate

# Prefer `$AAPL`; bare caps must skip common English and validate via Yahoo.
_TICKER_DOLLAR = re.compile(r"\$([A-Za-z]{1,5})\b")
_TICKER_CAPS = re.compile(r"\b([A-Z]{2,5})\b")
_STOP = frozenset(
    {
        "I", "A", "OK", "USD", "THE", "AND", "FOR", "ETF", "IPO", "YTD", "OTC",
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
        "ALSO", "VERY", "EVEN", "INTO", "FROM", "WITH", "HAVE", "THAN", "THEN",
        "THAT", "THIS", "THEY", "THEM", "WILL", "WOULD", "COULD", "SHOULD",
        "MIGHT", "MUST", "YOUR", "ABLE", "BACK", "CAME", "COME", "EACH", "ELSE",
        "GIVE", "KEEP", "KNOW", "LAST", "LEFT", "LONG", "LOOK", "MADE",
        "MOST", "MOVE", "OPEN", "OVER", "PART", "RISK", "SAFE",
        "SAME", "SEEM", "SHOW", "SUCH", "SURE", "TAKE", "TELL",
        "TOLD", "TURN", "USED", "WAYS", "WELL",
        "WENT", "BASE", "CASE", "DATA", "FACT", "FORM", "FULL",
        "HALF", "HIGH", "HOLD", "INFO", "KIND", "LINE", "MEAN", "NEXT",
        "REAL", "SIDE", "TRUE", "TYPE", "UNIT",
        "AREA", "AWAY", "BEST", "CALL", "HERE", "IDEA", "LATE", "WORD",
    }
)


def _yf_has_series(symbol: str) -> bool:
    try:
        t = yf.Ticker(symbol)
        h = t.history(period="5d")
        return h is not None and not h.empty
    except Exception:
        return False


def _pick_tickers(message: str) -> list[str]:
    raw = message.strip()
    explicit = [m.group(1).upper() for m in _TICKER_DOLLAR.finditer(raw)]
    caps = [t for t in _TICKER_CAPS.findall(raw.upper()) if t not in _STOP]
    ordered: list[str] = []
    seen: set[str] = set()
    for t in explicit + caps:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    # Symbols written as $XYZ are trusted. Bare ALLCAPS words must match a real series (filters "WHERE", etc.).
    tagged = frozenset(explicit)
    out: list[str] = []
    for t in ordered:
        if len(out) >= 3:
            break
        if t in tagged:
            out.append(t)
        elif _yf_has_series(t):
            out.append(t)
    return out


def _analyze_symbol(symbol: str) -> str:
    try:
        t = yf.Ticker(symbol)
        h = t.history(period="3mo")
        if h is None or h.empty:
            return f"{symbol}: no price history returned (check symbol or market hours)."
        close = h["Close"]
        last = Decimal(str(float(close.iloc[-1])))
        prev = Decimal(str(float(close.iloc[-2]))) if len(close) > 1 else last
        chg = last - prev
        pct = (chg / prev * 100) if prev != 0 else Decimal("0")
        sma20 = Decimal(str(float(close.tail(20).mean()))) if len(close) >= 5 else last
        info = getattr(t, "info", None) or {}
        name = info.get("shortName") or info.get("longName") or symbol
        cur = info.get("currency") or "USD"
        day_low = info.get("dayLow")
        day_high = info.get("dayHigh")
        fifty_two = info.get("fiftyTwoWeekHigh")
        fifty_two_l = info.get("fiftyTwoWeekLow")
        extras = []
        if day_low is not None and day_high is not None:
            extras.append(f"Session range: {day_low} – {day_high} {cur}")
        if fifty_two is not None and fifty_two_l is not None:
            extras.append(f"52w range: {fifty_two_l} – {fifty_two}")
        extra_txt = "\n".join(extras) if extras else ""
        trend = "above" if last > sma20 else "below"
        return (
            f"{name} ({symbol})\n"
            f"Last close: ~{last:.2f} {cur} ({chg:+.2f}, {pct:+.2f}% vs prev)\n"
            f"~20d SMA: {sma20:.2f} — price is {trend} short-term average.\n"
            f"{extra_txt}"
        )
    except Exception as e:
        return f"{symbol}: could not fetch market data ({e!s})."


def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
    _ = db
    tickers = _pick_tickers(message)

    rag_block = ""
    if rag_context and rag_context.strip():
        rag_block = "\n\n[Past context]\n" + rag_context.strip()[:2000]

    if not tickers:
        note = (
            "[Note: No valid Yahoo Finance symbols parsed. The user may be asking where to deploy a lump sum "
            "(e.g. savings) rather than naming tickers. Give general allocation and risk guidance in FinMate format; "
            "do not fabricate JSON-only replies or invented tickers. If they want quotes, ask them to name symbols "
            "like AAPL or use $AAPL.]"
        )
        enriched = f"{message}{rag_block}\n\n{note}"
        reply = generate(enriched)
        return AgentResult(
            agent=AgentName.INVESTMENT_ANALYSER,
            reply=reply,
            planned_steps=["resolve_tickers", "finmate_generate"],
            metadata={"tickers": "", "market_data": "none"},
        )

    market_data = "\n\n---\n\n".join(_analyze_symbol(sym) for sym in tickers)

    enriched = (
        f"{message}\n\n"
        f"[Live market data]\n{market_data}"
        f"{rag_block}"
    )

    reply = ensure_investment_reply_shape(
        generate(
            enriched,
            system_extra=SYSTEM_EXTRA_INVESTMENT,
            json_tools_fallback=["yfinance_lookup"],
        )
    )

    return AgentResult(
        agent=AgentName.INVESTMENT_ANALYSER,
        reply=reply,
        planned_steps=["resolve_tickers", "fetch_market_data", "compute_signals", "finmate_generate"],
        metadata={"tickers": ",".join(tickers)},
    )