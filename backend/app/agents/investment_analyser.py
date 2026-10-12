"""Investment Analyser — live Yahoo Finance data + data-driven replies (no generic filler)."""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.ticker_utils import extract_ticker_candidates, has_investment_signal, pick_validated_tickers
from app.agents.types import AgentName, AgentResult
from app.db.models import InvestmentHolding
from app.config import settings
from app.ml.finmate import SYSTEM_EXTRA_INVESTMENT, ensure_investment_reply_shape, generate
from app.services.market_data import fetch_history, get_ticker


def _extract_risk_from_context(ctx: str | None) -> str | None:
    if not ctx:
        return None
    m = re.search(r"risk tolerance\s*(?:is|:|=)\s*(low|conservative|moderate|medium|high|aggressive)", ctx, re.I)
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


def _extract_original_request(message: str) -> str:
    """Return the user's request without agentic observation metadata."""
    marker = "\n\n[Verified specialist observations]"
    if marker in message:
        return message.split(marker, 1)[0].strip()
    marker = "\n\nPrior verified specialist observations."
    if marker in message:
        return message.split(marker, 1)[0].strip()
    return message.strip()


def _extract_lump_sum(message: str) -> Decimal | None:
    """Extract an amount only when the wording ties it to investing."""
    amount_pattern = r"((?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)(?:\.\d{1,2})?)(?:\s*(k|m|lakh|lakhs)?)(?!\d|,\d)"
    currency_pattern = r"(?:₹|\$|€|£|\bINR\b|\bUSD\b|\bEUR\b|\bGBP\b)?\s*"
    investment_label = (
        r"\b(?:invest(?:ing)?|investment(?:\s+amount)?|lump[ -]?sum(?:\s+investment)?|"
        r"investable\s+(?:amount|surplus)|(?:fixed|term)[ -]?deposit)\b"
    )
    match = re.search(
        investment_label
        + r"\s*(?:(?:of|about|around|approximately|is)\s+)?"
        + currency_pattern
        + amount_pattern,
        message,
        re.I,
    )
    if not match:
        # Also support a currency amount followed immediately by an explicit
        # investment purpose, without matching unrelated invoice/expense amounts.
        match = re.search(
            currency_pattern
            + amount_pattern
            + r"\s+(?:to invest|for investing|as (?:a )?(?:lump[ -]?sum )?investment)\b",
            message,
            re.I,
        )
    if not match:
        return None
    try:
        num = Decimal(match.group(1).replace(",", ""))
    except Exception:
        return None
    mult = (match.group(2) or "").lower()
    if mult == "k":
        return num * Decimal("1000")
    if mult == "m":
        return num * Decimal("1000000")
    if mult in {"lakh", "lakhs"}:
        return num * Decimal("100000")
    return num if num >= 100 else None


def _is_portfolio_history_request(message: str) -> bool:
    return bool(
        re.search(
            r"\b(last|previous|past|current|existing|my)\b.*\b(investments?|portfolio|holdings?|positions?|returns?|profits?|gains?|losses?)\b",
            message,
            re.I,
        )
        or re.search(r"\b(investments?|portfolio|holdings?|positions?)\b.*\b(profit|profits|return|returns|gain|gains|loss|losses)\b", message, re.I)
    )


def _portfolio_history_reply(db: Session, user_id: UUID) -> AgentResult:
    holdings = db.scalars(
        select(InvestmentHolding)
        .where(InvestmentHolding.user_id == user_id)
        .order_by(InvestmentHolding.symbol.asc())
    ).all()
    if not holdings:
        return AgentResult(
            agent=AgentName.INVESTMENT_ANALYSER,
            reply=_no_portfolio_data_reply(),
            planned_steps=["check_portfolio_data", "report_data_unavailable"],
            metadata={"tickers": "", "market_data": "none", "source": "no_investment_data"},
        )

    lines: list[str] = []
    total_cost = Decimal("0")
    valued_cost = Decimal("0")
    total_value = Decimal("0")
    valued_count = 0
    currency = holdings[0].currency

    for holding in holdings:
        cost = holding.quantity * holding.average_cost
        total_cost += cost
        try:
            info = get_ticker(holding.symbol).info or {}
            raw = info.get("currentPrice") or info.get("regularMarketPrice")
            quote_currency = str(info.get("currency") or "").upper()
            if raw is None or quote_currency != holding.currency.upper():
                raise ValueError("current price unavailable or quote currency mismatch")
            price = Decimal(str(raw))
            value = holding.quantity * price
            profit = value - cost
            valued_cost += cost
            total_value += value
            valued_count += 1
            pct = (profit / cost * 100) if cost else Decimal("0")
            lines.append(
                f"{holding.symbol} | {holding.quantity:g} units | "
                f"Avg cost: {holding.average_cost:,.2f} {holding.currency} | "
                f"Current: {price:,.2f} {holding.currency} | "
                f"P/L: {profit:+,.2f} {holding.currency} ({pct:+.2f}%)"
            )
        except Exception:
            lines.append(
                f"{holding.symbol} | {holding.quantity:g} units | "
                f"Avg cost: {holding.average_cost:,.2f} {holding.currency} | "
                "Current market price unavailable"
            )

    holding_currencies = {h.currency.upper() for h in holdings}
    if len(holding_currencies) > 1:
        summary = (
            "Combined portfolio totals are omitted because holdings use multiple currencies. "
            "Convert all holdings to one reporting currency before aggregating."
        )
    elif valued_count:
        total_profit = total_value - valued_cost
        total_pct = (total_profit / valued_cost * 100) if valued_cost else Decimal("0")
        summary = (
            f"Total invested (valued holdings): {valued_cost:,.2f} {currency}\n"
            f"Current value: {total_value:,.2f} {currency}\n"
            f"Unrealized P/L: {total_profit:+,.2f} {currency} ({total_pct:+.2f}%)\n"
            f"Holdings valued: {valued_count}/{len(holdings)}"
        )
    else:
        summary = f"Portfolio cost basis: {total_cost:.2f} {currency}. Current market prices are unavailable or use a different currency, so P/L cannot be calculated."

    reply = (
        "[AGENT: INVESTMENT]\n\n"
        "Portfolio Snapshot\n\n"
        + "\n".join(lines)
        + "\n\n"
        + summary
        + "\n"
        + "Note: P/L shown here is unrealized because FinMate currently tracks holdings, "
        "not completed buy/sell transactions."
    )
    return AgentResult(
        agent=AgentName.INVESTMENT_ANALYSER,
        reply=reply,
        planned_steps=["check_portfolio_data", "fetch_current_prices", "calculate_unrealized_returns"],
        metadata={
            "tickers": ",".join(h.symbol for h in holdings),
            "market_data": "live" if valued_count else "unavailable",
            "source": "portfolio_holdings",
            "holdings_count": str(len(holdings)),
            "valued_holdings": str(valued_count),
        },
    )


def _no_portfolio_data_reply() -> str:
    return (
        "[AGENT: INVESTMENT]\n\n"
        "I don't have any stored investment holdings or investment transactions for your account yet, "
        "so I can't calculate your past investment profits or returns. "
        "You can add investment data when portfolio tracking is available, or ask me about a stock using "
        "a ticker such as AAPL for live market data.\n\n"
        '{"intent":"investment_history_unavailable","steps":["Check stored investment data","Add portfolio holdings or transactions","Analyze returns"],'
        '"tools_needed":[],"notes":"no investment holdings data is currently stored"}'
    )


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
    saved_risk = _extract_risk_from_context(rag_context)
    risk = saved_risk or "moderate"
    income = _extract_income_from_context(rag_context)
    location = _extract_location_from_context(rag_context)
    request = _extract_original_request(message)
    amount = _extract_lump_sum(request)
    eq, debt, cash = _allocation_for_risk(risk)

    parts: list[str] = (
        [f"Your saved risk tolerance is {saved_risk}."]
        if saved_risk
        else [
            "I couldn't verify your saved risk tolerance from the available context. "
            "The moderate-risk allocation below is illustrative, not personalized."
        ]
    )

    if amount is not None:
        eq_amt = (amount * Decimal(eq) / Decimal("100")).quantize(Decimal("1.00"))
        debt_amt = (amount * Decimal(debt) / Decimal("100")).quantize(Decimal("1.00"))
        cash_amt = (amount * Decimal(cash) / Decimal("100")).quantize(Decimal("1.00"))
        parts.append(
            f"For {amount:,.2f}, allocate ~{eq_amt:,.2f} to diversified equity, "
            f"~{debt_amt:,.2f} to debt/stable assets, and ~{cash_amt:,.2f} as liquidity."
        )
    elif income is not None:
        monthly_sip = (income * Decimal("0.25")).quantize(Decimal("1.00"))
        parts.append(
            f"With monthly income of {income:,.2f}, consider a {eq}/{debt}/{cash} "
            f"equity/debt/cash split and automate ~{monthly_sip:,.2f}/month via SIP or recurring buys."
        )
    else:
        parts.append(f"Consider a {eq}/{debt}/{cash} equity/debt/cash split.")

    if location and "india" in location.lower():
        parts.append(
            "In India, use broad index exposure (Nifty 50 / Sensex), short-duration debt funds for the bond sleeve, "
            "and keep the cash slice in savings or liquid funds."
        )
    else:
        parts.append("Use low-cost index funds for the equity sleeve and high-quality bonds or T-bills for the debt sleeve.")

    request = _extract_original_request(message)
    candidates = extract_ticker_candidates(request)
    if candidates and has_investment_signal(request):
        parts.append(
            f"I could not confirm live quotes for {', '.join(candidates)} right now — "
            "retry with `$TICKER` (e.g. `$MSFT`) or a company name like Microsoft."
        )
    elif has_investment_signal(request):
        parts.append("Name a stock (`$AAPL`) or company (e.g. Apple, Microsoft) for a live quote and trend read.")

    prose = " ".join(parts)
    return (
        "[AGENT: INVESTMENT]\n\n"
        f"{prose}\n\n"
        '{"intent":"portfolio_suggestion","steps":["Set allocation from risk profile","Use staggered entries","Rebalance quarterly"],'
        '"tools_needed":["yfinance_lookup"],"notes":"allocation is illustrative unless saved profile context is available; no ticker confirmed"}'
    )


def _visible_market_prose(reply: str) -> str:
    """Strip agent/JSON protocol lines before validating user-visible model prose."""
    visible: list[str] = []
    for line in (reply or "").replace("\r", "").splitlines():
        text = line.strip()
        if not text or (text.startswith("[AGENT:") and text.endswith("]")):
            continue
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = __import__("json").loads(text)
            except (ValueError, TypeError):
                pass
            else:
                if isinstance(parsed, dict):
                    continue
        visible.append(text)
    return "\n".join(visible).strip()


def _market_reply_covers_verified_facts(
    reply: str,
    analyses: list[_SymbolAnalysis],
    request: str,
) -> bool:
    """Reject a well-formatted answer that does not use the fetched market data."""
    prose = _visible_market_prose(reply).casefold().replace(",", "")
    if not prose:
        return False

    for analysis in analyses:
        if analysis.symbol.casefold() not in prose:
            return False
        if analysis.ok and analysis.last is not None and analysis.sma20 is not None:
            required_values = (f"{analysis.last:.2f}", f"{analysis.sma20:.2f}")
            if any(value.replace(",", "") not in prose for value in required_values):
                return False
        elif not any(marker in prose for marker in ("unavailable", "could not fetch", "no live price history")):
            return False

    # If the user named an investment amount, require the answer to address it.
    # Otherwise an unrelated but numerically fluent market summary could pass.
    amount = _extract_lump_sum(request)
    if amount is not None:
        variants = {
            f"{amount:,.2f}".replace(",", ""),
            f"{amount:.2f}".replace(",", ""),
            f"{amount:.0f}".replace(",", ""),
        }
        if not any(value in prose for value in variants):
            return False
    return True


def _data_driven_market_reply(
    analyses: list[_SymbolAnalysis],
    rag_context: str | None,
    request: str | None = None,
) -> str:
    """Build a readable answer from fetched market fields, never generated figures."""
    saved_risk = _extract_risk_from_context(rag_context)
    ok = [a for a in analyses if a.ok and a.last is not None and a.sma20 is not None]
    failed = [a.symbol for a in analyses if a not in ok]
    requested_amount = _extract_lump_sum(request or "")

    if not ok:
        failed_names = ", ".join(failed) or "the requested ticker"
        prose = (
            f"I couldn't retrieve usable recent market data for {failed_names}, so I can't give a "
            "data-backed price or trend assessment right now. Check the ticker and try again later."
        )
        if requested_amount is not None:
            prose += (
                f" You mentioned an amount of {requested_amount:,.2f}; confirm its currency and "
                "your investment time horizon before making a decision."
            )
        return (
            "[AGENT: INVESTMENT]\n\n"
            f"{prose}\n\n"
            '{"intent":"investment_info","steps":["Verify ticker","Retry market data","Reassess with available data"],'
            '"tools_needed":["yfinance_lookup"],"notes":"market data unavailable"}'
        )

    blocks: list[str] = []
    observations: list[str] = []
    for analysis in ok:
        blocks.append(analysis.text)
        assert analysis.last is not None and analysis.sma20 is not None
        trend = "above" if analysis.last > analysis.sma20 else "below or at"
        observations.append(
            f"{analysis.symbol} is {trend} its 20-day simple moving average. "
            "That is a short-term trend indicator, not a prediction or a buy/sell signal."
        )
        if analysis.pct is not None and abs(analysis.pct) >= Decimal("3"):
            direction = "up" if analysis.pct > 0 else "down"
            observations.append(
                f"{analysis.symbol} moved {abs(analysis.pct):.2f}% {direction} versus the previous session; "
                "treat that as short-term volatility, not a forecast."
            )

    notes: list[str] = []
    if failed:
        notes.append(f"Recent price data was unavailable for: {', '.join(failed)}.")
    if requested_amount is not None:
        notes.append(
            f"You mentioned an amount of {requested_amount:,.2f}. The quote alone cannot establish whether "
            "that amount is suitable; confirm the currency and consider your time horizon, risk tolerance, "
            "existing concentration, debt, and emergency savings before deciding."
        )
    if saved_risk:
        notes.append(f"Your saved risk tolerance is {saved_risk}.")
    else:
        notes.append("Your risk tolerance and investment time horizon are not confirmed in the available profile.")
    notes.append("This is market information, not a personalized recommendation to buy or sell.")

    prose = (
        "Latest available market snapshot:\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + " ".join(observations)
        + "\n\n"
        + " ".join(notes)
    )
    symbols = ",".join(a.symbol for a in ok)
    json_tail = (
        '{"intent":"investment_info","steps":["Review latest close and 20-day SMA","Check risk and time horizon",'
        '"Decide only after evaluating the whole portfolio"],"tools_needed":["yfinance_lookup"],'
        '"notes":"numbers and trend are taken from fetched market history for '
        + symbols
        + '"}'
    )
    return f"[AGENT: INVESTMENT]\n\n{prose}\n\n{json_tail}"

def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
    request = _extract_original_request(message)
    tickers = pick_validated_tickers(request)

    if not tickers:
        if _is_portfolio_history_request(request):
            return _portfolio_history_reply(db, user_id)
        reply = _portfolio_plan_reply(request, rag_context)
        return AgentResult(
            agent=AgentName.INVESTMENT_ANALYSER,
            reply=reply,
            planned_steps=["resolve_tickers", "personalized_allocation"],
            metadata={
                "tickers": "",
                "market_data": "none",
                "source": (
                    "saved_profile_context"
                    if (
                        _extract_risk_from_context(rag_context)
                        or _extract_income_from_context(rag_context) is not None
                        or _extract_location_from_context(rag_context)
                    )
                    else "illustrative_default"
                ),
            },
        )

    analyses = [_analyze_symbol(symbol) for symbol in tickers]
    available = [
        analysis
        for analysis in analyses
        if analysis.ok and analysis.last is not None and analysis.sma20 is not None
    ]

    if not available:
        reply = _data_driven_market_reply(analyses, rag_context, request)
        source = "market_data_unavailable"
    elif settings.finmate_use_llm:
        market_data = "\n\n---\n\n".join(analysis.text for analysis in analyses)
        rag_block = (
            "\n\n[Past context]\n" + rag_context.strip()[:2000]
            if rag_context and rag_context.strip()
            else ""
        )
        enriched = (
            f"Original user request: {request}\n\n"
            f"[Verified market data]\n{market_data}\n\n"
            "[Response requirements]\n"
            "- Answer the actual question, including any proposed amount.\n"
            "- For every available ticker, include its exact last close and 20-day SMA from the data above.\n"
            "- If the amount's currency, time horizon, or risk tolerance is unknown, say so rather than assume.\n"
            "- Do not invent prices, user holdings, expected returns, or a guaranteed outcome.\n"
            "- Use clear paragraphs and line breaks; do not output JSON-only generic steps.\n"
            f"{rag_block}"
        )
        try:
            model_reply = generate(
                enriched,
                system_extra=SYSTEM_EXTRA_INVESTMENT,
                json_tools_fallback=["yfinance_lookup"],
            )
            shaped_reply = ensure_investment_reply_shape(model_reply)
            if _market_reply_covers_verified_facts(shaped_reply, analyses, request):
                reply = shaped_reply
                source = "llm_verified_market_data"
            else:
                # The response-shape helper can produce generic fallback steps.
                # Prefer a factual deterministic answer when the model omits verified
                # prices, the moving average, a requested ticker, or the amount.
                reply = _data_driven_market_reply(analyses, rag_context, request)
                source = "verified_data_fallback"
        except Exception:
            reply = _data_driven_market_reply(analyses, rag_context, request)
            source = "verified_data_fallback"
    else:
        reply = _data_driven_market_reply(analyses, rag_context, request)
        source = "verified_data_fallback"

    return AgentResult(
        agent=AgentName.INVESTMENT_ANALYSER,
        reply=reply,
        planned_steps=["resolve_tickers", "fetch_market_data", "verify_market-grounding", "compose_reply"],
        metadata={
            "tickers": ",".join(tickers),
            "market_data": "live" if available else "unavailable",
            "source": source,
        },
    )
