"""Investment Analyser — live Yahoo Finance data + data-driven replies (no generic filler)."""

from __future__ import annotations

import json
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
        r"investable\s+(?:amount|surplus)|allocat(?:e|ed|ing|ion)|(?:fixed|term)[ -]?deposit)\b"
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


def _extract_lump_sum_currency(message: str) -> str | None:
    """Return a currency explicitly adjacent to the requested investment amount."""
    amount_pattern = (
        r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}|\d+)(?:\.\d{1,2})?"
        r"(?:\s*(?:k|m|lakh|lakhs)?)(?!\d|,\d)"
    )
    currency_token = r"₹|\$|€|£|\bINR\b|\bUSD\b|\bEUR\b|\bGBP\b"
    investment_label = (
        r"\b(?:invest(?:ing)?|investment(?:\s+amount)?|lump[ -]?sum(?:\s+investment)?|"
        r"investable\s+(?:amount|surplus)|allocat(?:e|ed|ing|ion)|(?:fixed|term)[ -]?deposit)\b"
    )
    match = re.search(
        investment_label
        + r"\s*(?:(?:of|about|around|approximately|is)\s+)?"
        + r"(?:(?P<prefix_currency>" + currency_token + r")\s*)?"
        + amount_pattern
        + r"\s*(?P<suffix_currency>" + currency_token + r")?",
        message,
        re.I,
    )
    if not match:
        return None
    token = match.group("prefix_currency") or match.group("suffix_currency")
    if not token:
        return None
    upper = token.upper()
    return {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}.get(token, upper)


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

        latest_session = h.index[-1]
        try:
            session_label = latest_session.date().isoformat()
        except (AttributeError, ValueError):
            session_label = str(latest_session)[:10]

        text = (
            f"{name} ({symbol})\n"
            f"Latest available session: {session_label}\n"
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
                parsed = json.loads(text)
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
    # Tool-router/debug fragments can contain the right numbers while still not
    # being a readable answer. Never accept those strings as verified prose.
    if re.search(
        r"\b(?:tokenized_tools|latest_[a-z0-9_]+_data|tools_needed|json_tools_fallback|"
        r"tool_call|tool_result|function_call)\b|\[\s*tokenized_tools\s*\]",
        prose,
        re.I,
    ):
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
    requested_currency = _extract_lump_sum_currency(request or "")

    if not ok:
        failed_names = ", ".join(failed) or "the requested ticker"
        prose = (
            f"I couldn't retrieve usable recent market data for {failed_names}, so I can't give a "
            "data-backed price or trend assessment right now. Check the ticker and try again later."
        )
        if requested_amount is not None:
            if requested_currency:
                prose += (
                    f" You specified {requested_currency} {requested_amount:,.2f}. "
                    "The requested investment amount is clear, but a decision still depends on your "
                    "time horizon, risk tolerance, existing concentration, debt, and emergency savings."
                )
            else:
                prose += (
                    f" You mentioned an amount of {requested_amount:,.2f} but did not specify its currency. "
                    "Confirm the currency and your investment time horizon before making a decision."
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
        quote_currencies = {
            match.group(1).upper()
            for analysis in ok
            if (match := re.search(r"Last close:\s*[\d,.]+\s+([A-Z]{3})", analysis.text, re.I))
        }
        if requested_currency and quote_currencies and requested_currency not in quote_currencies:
            quote_labels = ", ".join(sorted(quote_currencies))
            notes.append(
                f"You specified {requested_currency} {requested_amount:,.2f}, while the market quote currency "
                f"is {quote_labels}. These currencies differ, so no share count is estimated without a verified "
                "current exchange rate. The quote alone cannot establish suitability; consider your time horizon, "
                "risk tolerance, concentration, debt, and emergency savings."
            )
        elif requested_currency:
            notes.append(
                f"You specified {requested_currency} {requested_amount:,.2f}. The quote alone cannot establish "
                "suitability; consider your time horizon, risk tolerance, concentration, debt, and emergency savings."
            )
        else:
            notes.append(
                f"You mentioned an amount of {requested_amount:,.2f} without specifying its currency. "
                "Confirm the currency before comparing it with the quote; also consider your time horizon, "
                "risk tolerance, concentration, debt, and emergency savings."
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

def _portfolio_exposure_notes(portfolio: AgentResult, symbols: list[str]) -> list[str]:
    """State existing ticker exposure explicitly and compute a share only when possible."""
    saved_symbols = {
        symbol.strip().upper()
        for symbol in portfolio.metadata.get("tickers", "").split(",")
        if symbol.strip()
    }
    visible = _visible_market_prose(portfolio.reply)
    total_match = re.search(
        r"Current value:\s*([\d,]+(?:\.\d+)?)\s+([A-Z]{3})",
        visible,
        re.I,
    )
    portfolio_total = None
    portfolio_currency = None
    if total_match:
        try:
            portfolio_total = Decimal(total_match.group(1).replace(",", ""))
            portfolio_currency = total_match.group(2).upper()
        except Exception:
            portfolio_total = None

    notes: list[str] = []
    for symbol in symbols:
        key = symbol.upper()
        if key not in saved_symbols:
            notes.append(
                f"{key}: no existing holding is recorded in your saved portfolio; "
                "the market snapshot below is a comparison for a possible new position."
            )
            continue

        row = next(
            (line for line in visible.splitlines() if line.strip().upper().startswith(f"{key} |")),
            "",
        )
        row_match = re.search(
            rf"^{re.escape(key)}\s*\|\s*([\d.]+)\s+units\s+\|\s+Avg cost:.*?"
            rf"\|\s+Current:\s*([\d,]+(?:\.\d+)?)\s+([A-Z]{{3}})",
            row,
            re.I,
        )
        try:
            if row_match:
                quantity = Decimal(row_match.group(1))
                price = Decimal(row_match.group(2).replace(",", ""))
                quote_currency = row_match.group(3).upper()
                position_value = quantity * price
                if (
                    portfolio_total is not None
                    and portfolio_total > 0
                    and portfolio_currency == quote_currency
                ):
                    share = position_value / portfolio_total * Decimal("100")
                    notes.append(
                        f"{key}: existing holding value is approximately "
                        f"{position_value:,.2f} {quote_currency} ({share:.2f}% of the valued portfolio)."
                    )
                else:
                    notes.append(
                        f"{key}: an existing holding is recorded, but its share of the portfolio "
                        "cannot be calculated reliably from the available same-currency valuations."
                    )
            else:
                notes.append(
                    f"{key}: an existing holding is recorded, but its current value is unavailable."
                )
        except Exception:
            notes.append(
                f"{key}: an existing holding is recorded, but its current value is unavailable."
            )
    return notes


def _is_market_source_audit_request(message: str) -> bool:
    """Recognize data-provenance audits that mention market quotes but ask no investment question."""
    text = _extract_original_request(message)
    return bool(
        re.search(r"\b(?:audit|identify|distinguish|trace|classify|verify)\b", text, re.I)
        and re.search(
            r"\b(?:which figures|which values|data source|source of|supported by|from my|from the)\b",
            text,
            re.I,
        )
        and re.search(
            r"\b(?:market quotes?|market data|uploaded receipt|recorded transactions?)\b",
            text,
            re.I,
        )
        and not re.search(
            r"\b(?:should i invest|buy|sell|allocate|investment amount|"
            r"portfolio allocation|stock comparison)\b",
            text,
            re.I,
        )
    )




def _market_quote_evidence_lines(context: str | None) -> list[str]:
    """Read a prior FinMate quote snapshot from recent context without inventing missing fields."""
    if not context:
        return []
    snapshot = re.search(r"Latest available market snapshot\s*:", context, re.I)
    if not snapshot:
        return []
    text = context[snapshot.end():]
    stop = re.search(
        r"(?im)^\s*(?:Existing ticker exposure:|Your saved risk tolerance\b|Market data describes\b|"
        r"[A-Z]{1,6} is above\b)",
        text,
    )
    if stop:
        text = text[:stop.start()]
    headers = list(re.finditer(r"(?m)^([^\n]*?)\s+\(([A-Z][A-Z0-9.-]{0,5})\)\s*$", text))
    evidence: list[str] = []
    for index, header in enumerate(headers):
        block_end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        block = text[header.end():block_end]
        symbol = header.group(2).upper()
        session = re.search(r"Latest available session\s*:\s*(\d{4}-\d{2}-\d{2})", block, re.I)
        close = re.search(
            r"Last close\s*:\s*([\d,]+(?:\.\d+)?)\s+([A-Z]{3})\s*(?:\(([^)]*)\))?",
            block,
            re.I,
        )
        sma = re.search(r"20-day SMA\s*:\s*([\d,]+(?:\.\d+)?)", block, re.I)
        if not session or not close:
            continue
        detail = (
            f"- {symbol}: session {session.group(1)}, last close "
            f"{close.group(1)} {close.group(2).upper()}"
        )
        if close.group(3):
            detail += f" ({close.group(3).strip()})"
        if sma:
            detail += f", 20-day SMA {sma.group(1)}"
        detail += ". This is a prior FinMate market snapshot from recent conversation context."
        evidence.append(detail)
    return evidence

def run(
    user_id: UUID,
    message: str,
    db: Session,
    rag_context: str | None = None,
) -> AgentResult:
    request = _extract_original_request(message)
    tickers = pick_validated_tickers(request)

    # Portfolio intent wins even when the user also names a ticker. Preserve the
    # saved-position audit, then add a deterministic market snapshot for comparison.
    if _is_portfolio_history_request(request):
        portfolio = _portfolio_history_reply(db, user_id)
        if not tickers:
            return portfolio
        analyses = [_analyze_symbol(symbol) for symbol in tickers]
        market_reply = _data_driven_market_reply(analyses, rag_context, request)
        portfolio_prose = _visible_market_prose(portfolio.reply)
        market_prose = _visible_market_prose(market_reply)
        exposure_lines = _portfolio_exposure_notes(portfolio, tickers)
        reply = (
            "[AGENT: INVESTMENT]\n\n"
            "Saved portfolio snapshot:\n\n"
            f"{portfolio_prose}\n\n"
            "Requested market comparison:\n\n"
            f"{market_prose}\n\n"
            "Existing ticker exposure:\n"
            + "\n".join(f"- {line}" for line in exposure_lines)
            + "\n\n"
            "Market data describes the named stock; it does not imply that an additional purchase is suitable."
        )
        metadata = dict(portfolio.metadata)
        metadata.update(
            {
                "tickers": ",".join(tickers),
                "requested_tickers": ",".join(tickers),
                "market_data": "live" if any(analysis.ok for analysis in analyses) else "unavailable",
                "source": "portfolio_market_comparison",
            }
        )
        return AgentResult(
            agent=AgentName.INVESTMENT_ANALYSER,
            reply=reply,
            planned_steps=[
                "load_saved_portfolio",
                "value_existing_holdings",
                "fetch_requested_market_data",
                "compare_existing_ticker_exposure",
            ],
            metadata=metadata,
        )

    if not tickers:
        if _is_market_source_audit_request(request):
            quote_evidence = _market_quote_evidence_lines(rag_context)
            if quote_evidence:
                reply = (
                    "[AGENT: INVESTMENT]\n\n"
                    "Market quote evidence found in recent conversation context:\n"
                    + "\n".join(quote_evidence)
                    + "\n\nProvenance limitation: the recent FinMate snapshot records the ticker, trading-session date, "
                    "close, and (where present) 20-day SMA, but this audit context does not retain the raw provider "
                    "response or request identifier. These are market-price figures, not recorded transactions, "
                    "and no INR conversion has been assumed. No buy/sell recommendation was generated."
                )
                return AgentResult(
                    agent=AgentName.INVESTMENT_ANALYSER,
                    reply=reply,
                    planned_steps=["inspect_recent_quote_context", "classify_quote_provenance"],
                    metadata={
                        "tickers": ",".join(re.findall(r"^- ([A-Z][A-Z0-9.-]{0,5}):", "\n".join(quote_evidence), re.M)),
                        "market_data": "recent_context",
                        "source": "market_source_audit_context",
                    },
                )

            saved_symbols = db.scalars(
                select(InvestmentHolding.symbol)
                .where(InvestmentHolding.user_id == user_id)
                .order_by(InvestmentHolding.symbol.asc())
            ).all()
            symbol_note = (
                " Saved portfolio symbols found: " + ", ".join(str(symbol).upper() for symbol in saved_symbols) + "."
                if saved_symbols else
                " No saved portfolio symbols were found."
            )
            reply = (
                "[AGENT: INVESTMENT]\n\n"
                "Market-quote provenance is UNVERIFIED: I did not find a dated quote snapshot in the retrieved "
                "conversation context, and this request does not include a ticker or raw quote record."
                + symbol_note
                + " A saved holding identifies a ticker, not a historical price source. This is a missing-evidence "
                "finding; it does not mean market data never existed. I have not substituted a fresh quote for a "
                "historical quote or generated a buy/sell recommendation."
            )
            return AgentResult(
                agent=AgentName.INVESTMENT_ANALYSER,
                reply=reply,
                planned_steps=["inspect_recent_quote_context", "report_missing_quote_provenance"],
                metadata={
                    "tickers": ",".join(str(symbol).upper() for symbol in saved_symbols),
                    "market_data": "not_found_in_context",
                    "source": "market_source_audit_unverified",
                },
            )
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
