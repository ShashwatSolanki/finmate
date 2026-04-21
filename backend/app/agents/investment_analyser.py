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
        "SAME", "SEEM", "SHOW", "SUCH", "SURE", "TAKE", "TELL", "LEVEL",
        "TOLD", "TURN", "USED", "WAYS", "WELL",
        "WENT", "BASE", "CASE", "DATA", "FACT", "FORM", "FULL",
        "HALF", "HIGH", "HOLD", "INFO", "KIND", "LINE", "MEAN", "NEXT",
        "REAL", "SIDE", "TRUE", "TYPE", "UNIT",
        "AREA", "AWAY", "BEST", "CALL", "HERE", "IDEA", "LATE", "WORD",
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
}

_KNOWN_TICKERS = frozenset(
    {
        "AAPL",
        "MSFT",
        "GOOGL",
        "AMZN",
        "TSLA",
        "NVDA",
        "META",
    }
)


def _extract_risk_from_context(ctx: str | None) -> str | None:
    if not ctx:
        return None
    m = re.search(r"risk tolerance:\s*(low|conservative|moderate|medium|high|aggressive)", ctx, re.I)
    if not m:
        return None
    v = m.group(1).lower()
    if v in {"medium"}:
        return "moderate"
    if v in {"low", "conservative"}:
        return "conservative"
    if v in {"high", "aggressive"}:
        return "aggressive"
    return v


def _extract_income_from_context(ctx: str | None) -> Decimal | None:
    if not ctx:
        return None
    m = re.search(r"monthly income:\s*([\d,]+(?:\.\d+)?)", ctx, re.I)
    if not m:
        return None
    return Decimal(m.group(1).replace(",", ""))


def _extract_location_from_context(ctx: str | None) -> str | None:
    if not ctx:
        return None
    m = re.search(r"location:\s*([^\n]+)", ctx, re.I)
    return m.group(1).strip() if m else None


def _extract_lump_sum(message: str) -> Decimal | None:
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*([kKmM]|lakh|lakhs)?\b", message)
    if not m:
        return None
    num = Decimal(m.group(1))
    mult = (m.group(2) or "").lower()
    if mult == "k":
        return num * Decimal("1000")
    if mult == "m":
        return num * Decimal("1000000")
    if mult in {"lakh", "lakhs"}:
        return num * Decimal("100000")
    if num < 100:  # likely duration or count, not money
        return None
    return num


def _allocation_for_risk(risk: str | None) -> tuple[int, int, int]:
    if risk == "aggressive":
        return (75, 20, 5)  # equity, debt, cash
    if risk == "conservative":
        return (40, 45, 15)
    return (60, 30, 10)  # moderate default


def _plain_investment_plan(message: str, rag_context: str | None) -> str:
    t = message.lower()
    risk = _extract_risk_from_context(rag_context)
    income = _extract_income_from_context(rag_context)
    location = _extract_location_from_context(rag_context)
    amount = _extract_lump_sum(message)
    eq, debt, cash = _allocation_for_risk(risk)
    india_hint = (
        "Use broad index funds (Nifty 50/Sensex), short-duration debt funds or high-quality debt options, "
        "and keep your cash buffer in savings/liquid instruments. "
        if location and "india" in location.lower()
        else ""
    )

    if "daily" in t or "ration" in t or "usage" in t:
        base = amount if amount is not None else income
        if base is None:
            amount_text = (
                "First separate spending money from investing money: keep 10-15% for short-term liquidity, "
                "then invest the rest with your risk-based split."
            )
        else:
            daily = (base / Decimal("30")).quantize(Decimal("1.00"))
            invest = (base * Decimal("0.70")).quantize(Decimal("1.00"))
            expenses = (base * Decimal("0.20")).quantize(Decimal("1.00"))
            buffer = (base * Decimal("0.10")).quantize(Decimal("1.00"))
            amount_text = (
                f"From {base:,.2f}, keep ~{expenses:,.2f} for monthly spending "
                f"(~{daily:,.2f}/day), invest ~{invest:,.2f}, and keep ~{buffer:,.2f} as emergency liquidity."
            )
        plan_suffix = "Review this split monthly as your expenses change."
    elif amount is not None:
        eq_amt = (amount * Decimal(eq) / Decimal("100")).quantize(Decimal("1.00"))
        debt_amt = (amount * Decimal(debt) / Decimal("100")).quantize(Decimal("1.00"))
        cash_amt = (amount * Decimal(cash) / Decimal("100")).quantize(Decimal("1.00"))
        amount_text = (
            f"For {amount:,.2f}, a practical split is ~{eq_amt:,.2f} to diversified equity funds, "
            f"~{debt_amt:,.2f} to safer debt instruments, and ~{cash_amt:,.2f} as liquidity."
        )
        plan_suffix = "Build positions in 3-4 staggered buys over the month and rebalance quarterly."
    else:
        amount_text = (
            f"Use a {eq}/{debt}/{cash} split: {eq}% diversified equity, {debt}% debt, {cash}% cash buffer."
        )
        if income is not None:
            investable = (income * Decimal("0.30")).quantize(Decimal("1.00"))
            amount_text += f" With monthly income {income:,.2f}, start by investing ~{investable:,.2f}/month via SIP."
        plan_suffix = "Start with SIPs so you can stay consistent across market cycles."

    risk_text = f"Using your {risk} risk profile, " if risk else ""
    return (
        "[AGENT: INVESTMENT]\n\n"
        f"{risk_text}{amount_text} {india_hint}{plan_suffix}\n\n"
        '{"intent":"portfolio_suggestion","steps":["Set allocation by risk profile","Invest in staggered tranches","Rebalance every quarter"],'
        '"tools_needed":["yfinance_lookup"],"notes":"used onboarding-aware deterministic fallback"}'
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
    lower = raw.lower()
    explicit = [m.group(1).upper() for m in _TICKER_DOLLAR.finditer(raw)]
    name_hits: list[str] = []
    company_words_upper: set[str] = set()
    for name, symbol in _COMPANY_TO_TICKER.items():
        if name in lower:
            name_hits.append(symbol)
            company_words_upper.add(name.upper())
    dynamic_stop = _STOP | company_words_upper | {"TERM"}
    caps = [t for t in _TICKER_CAPS.findall(raw.upper()) if t not in dynamic_stop]
    ordered: list[str] = []
    seen: set[str] = set()
    for t in explicit + caps + name_hits:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    # Symbols written as $XYZ and known company-name mappings are trusted.
    # Bare ALLCAPS words must match a real series (filters "WHERE", etc.).
    tagged = frozenset(explicit)
    mapped = frozenset(name_hits)
    known = _KNOWN_TICKERS | mapped
    out: list[str] = []
    for t in ordered:
        if len(out) >= 3:
            break
        if t in tagged or t in known:
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
        reply = _plain_investment_plan(message, rag_context)
        return AgentResult(
            agent=AgentName.INVESTMENT_ANALYSER,
            reply=reply,
            planned_steps=["resolve_tickers", "finmate_generate"],
            metadata={"tickers": "", "market_data": "none"},
        )

    market_data = "\n\n---\n\n".join(_analyze_symbol(sym) for sym in tickers)
    forcing_instructions = (
        "[Response requirements]\n"
        "- Reference at least two concrete numbers from the live data for each ticker discussed.\n"
        "- Explicitly mention last close and 20d SMA relationship (above/below).\n"
        "- If you suggest waiting or buying slowly, tie it to the observed price/trend values.\n"
        "- Keep recommendations risk-aware, but do not ignore provided market data.\n"
    )

    enriched = (
        f"{message}\n\n"
        f"[Live market data]\n{market_data}"
        f"\n\n{forcing_instructions}"
        f"{rag_block}"
    )

    try:
        model_reply = generate(
            enriched,
            system_extra=SYSTEM_EXTRA_INVESTMENT,
            json_tools_fallback=["yfinance_lookup"],
        )
    except Exception:
        model_reply = (
            "[AGENT: INVESTMENT]\n\n"
            "The current market snapshot suggests using staggered entries instead of lump-sum timing bets. "
            "Use diversification and position sizing aligned to your risk tolerance.\n\n"
            '{"intent":"portfolio_suggestion","steps":["Review live quote and SMA trend","Define allocation bands","Use staggered buys"],'
            '"tools_needed":["yfinance_lookup"],"notes":"fallback response"}'
        )
    reply = ensure_investment_reply_shape(model_reply)

    return AgentResult(
        agent=AgentName.INVESTMENT_ANALYSER,
        reply=reply,
        planned_steps=["resolve_tickers", "fetch_market_data", "compute_signals", "finmate_generate"],
        metadata={"tickers": ",".join(tickers)},
    )