"""Investment Analyser — live Yahoo Finance data + data-driven replies (no generic filler)."""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.ticker_utils import extract_ticker_candidates, has_investment_signal, pick_validated_tickers
from app.agents.types import AgentName, AgentResult
from app.config import settings
from app.ml.finmate import SYSTEM_EXTRA_INVESTMENT, ensure_investment_reply_shape, generate
from app.services.market_data import fetch_history, get_ticker


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
    if num < 100:
        return None
    return num


def _allocation_for_risk(risk: str | None) -> tuple[int, int, int]:
    if risk == "aggressive":
        return (75, 20, 5)
    if risk == "conservative":
        return (40, 45, 15)
    return (60, 30, 10)


class _SymbolAnalysis:
    def __init__(self, symbol: str, ok: bool, text: str, last: Decimal | None, sma20: Decimal | None, pct: Decimal | None):
        self.symbol = symbol
        self.ok = ok
        self.text = text
        self.last = last
        self.sma20 = sma20
        self.pct = pct


def _analyze_symbol(symbol: str) -> _SymbolAnalysis:
    try:
        t = get_ticker(symbol)
        h = fetch_history(symbol, period="3mo")
        if h is None or h.empty:
            return _SymbolAnalysis(symbol, False, f"{symbol}: no live price history available.", None, None, None)

        close = h["Close"]
        last = Decimal(str(float(close.iloc[-1])))
        prev = Decimal(str(float(close.iloc[-2]))) if len(close) > 1 else last
        chg = last - prev
        pct = (chg / prev * 100) if prev != 0 else Decimal("0")
        sma20 = Decimal(str(float(close.tail(20).mean()))) if len(close) >= 5 else last

        info: dict = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}

        name = info.get("shortName") or info.get("longName") or symbol
        cur = info.get("currency") or "USD"
        trend = "above" if last > sma20 else "below"
        extras: list[str] = []
        day_low, day_high = info.get("dayLow"), info.get("dayHigh")
        if day_low is not None and day_high is not None:
            extras.append(f"Session range: {day_low} – {day_high} {cur}")
        fifty_two, fifty_two_l = info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow")
        if fifty_two is not None and fifty_two_l is not None:
            extras.append(f"52-week range: {fifty_two_l} – {fifty_two} {cur}")

        text = (
            f"{name} ({symbol})\n"
            f"Last close: {last:.2f} {cur} ({chg:+.2f}, {pct:+.2f}% vs previous session)\n"
            f"20-day SMA: {sma20:.2f} — trading {trend} the short-term average."
        )
        if extras:
            text += "\n" + "\n".join(extras)
        return _SymbolAnalysis(symbol, True, text, last, sma20, pct)
    except Exception as exc:
        return _SymbolAnalysis(symbol, False, f"{symbol}: could not fetch market data ({exc!s}).", None, None, None)


def _portfolio_plan_reply(message: str, rag_context: str | None) -> str:
    """Personalized allocation when no ticker — uses onboarding numbers, not boilerplate."""
    risk = _extract_risk_from_context(rag_context) or "moderate"
    income = _extract_income_from_context(rag_context)
    location = _extract_location_from_context(rag_context)
    amount = _extract_lump_sum(message)
    eq, debt, cash = _allocation_for_risk(risk)

    parts: list[str] = [f"Using your {risk} risk profile"]

    if amount is not None:
        eq_amt = (amount * Decimal(eq) / Decimal("100")).quantize(Decimal("1.00"))
        debt_amt = (amount * Decimal(debt) / Decimal("100")).quantize(Decimal("1.00"))
        cash_amt = (amount * Decimal(cash) / Decimal("100")).quantize(Decimal("1.00"))
        parts.append(
            f"for {amount:,.2f}: allocate ~{eq_amt:,.2f} to diversified equity, "
            f"~{debt_amt:,.2f} to debt/stable assets, and ~{cash_amt:,.2f} as liquidity."
        )
    elif income is not None:
        monthly_sip = (income * Decimal("0.25")).quantize(Decimal("1.00"))
        parts.append(
            f"with monthly income {income:,.2f}: target a {eq}/{debt}/{cash} equity/debt/cash split "
            f"and automate ~{monthly_sip:,.2f}/month via SIP or recurring buys."
        )
    else:
        parts.append(f"target a {eq}/{debt}/{cash} equity/debt/cash split.")

    if location and "india" in location.lower():
        parts.append(
            "In India, use broad index exposure (Nifty 50 / Sensex), short-duration debt funds for the bond sleeve, "
            "and keep the cash slice in savings or liquid funds."
        )
    else:
        parts.append("Use low-cost index funds for the equity sleeve and high-quality bonds or T-bills for the debt sleeve.")

    candidates = extract_ticker_candidates(message)
    if candidates and has_investment_signal(message):
        parts.append(
            f"I could not confirm live quotes for {', '.join(candidates)} right now — "
            "retry with `$TICKER` (e.g. `$MSFT`) or a company name like Microsoft."
        )
    elif has_investment_signal(message):
        parts.append("Name a stock (`$AAPL`) or company (e.g. Apple, Microsoft) for a live quote and trend read.")

    prose = " ".join(parts)
    return (
        "[AGENT: INVESTMENT]\n\n"
        f"{prose}\n\n"
        '{"intent":"portfolio_suggestion","steps":["Set allocation from risk profile","Use staggered entries","Rebalance quarterly"],'
        '"tools_needed":["yfinance_lookup"],"notes":"personalized from onboarding; no ticker confirmed"}'
    )


def _data_driven_market_reply(analyses: list[_SymbolAnalysis], rag_context: str | None) -> str:
    """Build prose strictly from fetched market fields — never generic investing advice."""
    risk = _extract_risk_from_context(rag_context) or "moderate"
    ok = [a for a in analyses if a.ok and a.last is not None]
    if not ok:
        failed = ", ".join(a.symbol for a in analyses)
        return (
            "[AGENT: INVESTMENT]\n\n"
            f"Live quotes for {failed} are unavailable at the moment. "
            "Check the symbol spelling or try again in a few minutes.\n\n"
            '{"intent":"investment_info","steps":["Verify ticker symbol","Retry market data fetch","Decide entry size"],'
            '"tools_needed":["yfinance_lookup"],"notes":"market data unavailable"}'
        )

    blocks: list[str] = []
    actions: list[str] = []
    for a in ok:
        blocks.append(a.text)
        assert a.last is not None and a.sma20 is not None
        if a.last > a.sma20:
            actions.append(
                f"{a.symbol} is above its 20-day SMA ({a.sma20:.2f}); momentum is positive — "
                f"if you buy, scale in rather than lump-sum at {a.last:.2f}."
            )
        else:
            actions.append(
                f"{a.symbol} is below its 20-day SMA ({a.sma20:.2f}); "
                f"last close {a.last:.2f} — consider waiting for stabilization or smaller tranches."
            )
        if a.pct is not None and abs(a.pct) >= Decimal("3"):
            direction = "up" if a.pct > 0 else "down"
            actions.append(f"{a.symbol} moved {abs(a.pct):.2f}% {direction} vs the prior session — avoid chasing.")

    risk_note = (
        f"Given your {risk} risk tolerance, keep position sizes modest relative to your total portfolio "
        "and maintain an emergency fund outside these names."
    )

    prose = (
        "Live market snapshot:\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + " ".join(actions)
        + " "
        + risk_note
    )
    symbols = ",".join(a.symbol for a in ok)
    json_tail = (
        '{"intent":"investment_info","steps":["Review last close vs 20d SMA","Size position to risk profile",'
        '"Use staggered entries"],"tools_needed":["yfinance_lookup"],"notes":"built from live yfinance data for '
        + symbols
        + '"}'
    )
    return (
        "[AGENT: INVESTMENT]\n\n"
        f"{prose}\n\n"
        f"{json_tail}"
    )


def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
    _ = db
    tickers = pick_validated_tickers(message)

    rag_block = ""
    if rag_context and rag_context.strip():
        rag_block = "\n\n[Past context]\n" + rag_context.strip()[:2000]

    if not tickers:
        reply = _portfolio_plan_reply(message, rag_context)
        return AgentResult(
            agent=AgentName.INVESTMENT_ANALYSER,
            reply=reply,
            planned_steps=["resolve_tickers", "personalized_allocation"],
            metadata={"tickers": "", "market_data": "none", "source": "onboarding_data"},
        )

    analyses = [_analyze_symbol(sym) for sym in tickers]
    market_data = "\n\n---\n\n".join(a.text for a in analyses)

    if settings.finmate_use_llm:
        enriched = (
            f"{message}\n\n"
            f"[Live market data]\n{market_data}\n\n"
            "[Response requirements]\n"
            "- Quote at least two numbers from the live data per ticker.\n"
            "- State last close and whether price is above or below the 20-day SMA.\n"
            "- Tie any buy/wait advice to those numbers.\n"
            f"{rag_block}"
        )
        try:
            model_reply = generate(
                enriched,
                system_extra=SYSTEM_EXTRA_INVESTMENT,
                json_tools_fallback=["yfinance_lookup"],
            )
            reply = ensure_investment_reply_shape(model_reply)
            source = "llm"
        except Exception:
            reply = _data_driven_market_reply(analyses, rag_context)
            source = "live_data"
    else:
        reply = _data_driven_market_reply(analyses, rag_context)
        source = "live_data"

    return AgentResult(
        agent=AgentName.INVESTMENT_ANALYSER,
        reply=reply,
        planned_steps=["resolve_tickers", "fetch_market_data", "compute_signals", "compose_reply"],
        metadata={"tickers": ",".join(tickers), "market_data": "live", "source": source},
    )
