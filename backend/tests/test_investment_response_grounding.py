"""Regression tests for model responses grounded in verified quote data."""

from decimal import Decimal
from uuid import uuid4

from app.agents import investment_analyser


def test_generic_llm_investment_steps_are_replaced_with_verified_market_facts(database, monkeypatch):
    analysis = investment_analyser._SymbolAnalysis(
        "MSFT",
        True,
        (
            "Microsoft Corporation (MSFT)\n"
            "Latest available session: 2026-10-09\n"
            "Last close: 421.25 USD (+2.40, +0.57% vs previous session)\n"
            "20-day SMA: 417.80 — trading above the short-term average."
        ),
        Decimal("421.25"),
        Decimal("417.80"),
        Decimal("0.57"),
    )
    generic_json = (
        '{"intent":"investment_info","steps":['
        '"Clarify time horizon and risk tolerance",'
        '"Favor diversified low-cost index exposure",'
        '"Keep emergency cash separate from long-term investments"],'
        '"tools_needed":["yfinance_lookup"],"notes":"generic fallback"}'
    )

    monkeypatch.setattr(investment_analyser, "pick_validated_tickers", lambda message: ["MSFT"])
    monkeypatch.setattr(investment_analyser, "_analyze_symbol", lambda symbol: analysis)
    monkeypatch.setattr(investment_analyser, "generate", lambda *args, **kwargs: generic_json)
    monkeypatch.setattr(investment_analyser.settings, "finmate_use_llm", True)

    with database() as db:
        result = investment_analyser.run(
            uuid4(),
            "Should I invest 10000 on Microsoft?",
            db,
        )

    assert result.metadata["source"] == "verified_data_fallback"
    assert "MSFT" in result.reply
    assert "421.25" in result.reply
    assert "417.80" in result.reply
    assert "10,000.00" in result.reply
    assert "couldn't determine whether" not in result.reply.casefold()
    assert "Clarify time horizon and risk tolerance. Favor diversified low-cost index exposure." not in result.reply


def test_market_reply_grounding_rejects_generic_text_without_quote_numbers():
    analysis = investment_analyser._SymbolAnalysis(
        "MSFT",
        True,
        "Microsoft Corporation (MSFT)\nLast close: 421.25 USD\n20-day SMA: 417.80",
        Decimal("421.25"),
        Decimal("417.80"),
        Decimal("0.57"),
    )

    assert not investment_analyser._market_reply_covers_verified_facts(
        "[AGENT: INVESTMENT]\n\nClarify time horizon and risk tolerance.\n\n"
        '{"intent":"investment_info","steps":["Review investments"],"tools_needed":[],"notes":"generic"}',
        [analysis],
        "Should I invest 10000 on Microsoft?",
    )
    assert investment_analyser._market_reply_covers_verified_facts(
        "[AGENT: INVESTMENT]\n\n"
        "MSFT last close was 421.25 USD and its 20-day SMA was 417.80. "
        "You mentioned 10,000.00 as the amount to invest.\n\n"
        '{"intent":"investment_info","steps":["Review latest close"],"tools_needed":[],"notes":"grounded"}',
        [analysis],
        "Should I invest 10000 on Microsoft?",
    )


def test_market_reply_grounding_rejects_raw_tool_trace_even_when_numbers_match():
    analysis = investment_analyser._SymbolAnalysis(
        "MSFT",
        True,
        "Microsoft Corporation (MSFT)\nLast close: 541.34 USD\n20-day SMA: 511.90",
        Decimal("541.34"),
        Decimal("511.90"),
        Decimal("1.17"),
    )

    raw_tool_output = (
        '[tokenized_tools]="yfinance_lookup", '
        '[latest_msft_data]=["Close: 541.34", "SMA: 511.90"], [...]'
    )
    assert not investment_analyser._market_reply_covers_verified_facts(
        raw_tool_output,
        [analysis],
        "Review my portfolio and compare it with Microsoft (MSFT).",
    )
