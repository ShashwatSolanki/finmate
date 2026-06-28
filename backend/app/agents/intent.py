"""Hybrid intent: keyword signals + ticker detection + embedding similarity."""

from __future__ import annotations

import re
from functools import lru_cache

import numpy as np

from app.agents.ticker_utils import extract_ticker_candidates, has_investment_signal
from app.agents.types import AgentName
from app.config import settings
from app.ml.embeddings import encode_texts

_BUDGET = re.compile(
    r"\b(budget|spend|spending|expense|expenses|save money|cut cost|monthly|category|"
    r"overspend|saving|savings|groceries|rent|utilities|bills|income|outgoings|"
    r"transaction|transactions|spent|spending habits)\b",
    re.I,
)
_INVOICE = re.compile(
    r"\b(invoice|invoices|bill client|receipt|pdf bill|generate invoice|itemize|"
    r"line items?|billing|client invoice|net 30|net 15|payment terms|due on receipt|"
    r"freelance invoice|bill for|send an invoice)\b",
    re.I,
)
_INVEST = re.compile(
    r"\b(stock|stocks|ticker|portfolio|invest|investing|investment|equity|nasdaq|nyse|"
    r"quote|dividend|pe ratio|volatility|sma|chart|should i buy|buy now|market|shares|"
    r"etf|index fund|mutual fund|sip|allocate|allocation|nifty|sensex)\b",
    re.I,
)
_TICKER_IN_TEXT = re.compile(r"\$[A-Za-z]{1,5}\b|\b(AAPL|MSFT|TSLA|GOOGL|AMZN|NVDA|META|NFLX|AMD|INTC)\b")

PROTOTYPES: dict[AgentName, list[str]] = {
    AgentName.BUDGET_PLANNER: [
        "Help me reduce my monthly spending and stick to a budget.",
        "How much did I spend on food and dining last month?",
        "I need a plan to save money and track expenses by category.",
        "Compare my spending this month to last month.",
        "My rent is too high, how do I manage my expenses?",
        "I am overspending on groceries every month.",
        "Show my transaction summary for the last 30 days.",
    ],
    AgentName.INVOICE_GENERATOR: [
        "Generate a PDF invoice for my freelance client with line items.",
        "Create a bill with amounts and descriptions for services rendered.",
        "I need an itemized invoice template for accounting.",
        "Make a professional invoice for web development work.",
        "Bill my client for consulting — 1200 design, 400 hosting.",
    ],
    AgentName.INVESTMENT_ANALYSER: [
        "Analyze AAPL stock price trend and moving averages.",
        "What is the latest quote and historical volatility for MSFT?",
        "Should I look at portfolio risk and equity exposure?",
        "How is Microsoft stock performing this quarter?",
        "I have 10000 to invest for 5 years with moderate risk.",
        "Compare GOOGL and AMZN for a long-term portfolio.",
    ],
}


def _keyword_vector(text: str) -> dict[AgentName, float]:
    t = text.strip()
    scores = {
        AgentName.BUDGET_PLANNER: float(len(_BUDGET.findall(t))),
        AgentName.INVOICE_GENERATOR: float(len(_INVOICE.findall(t))),
        AgentName.INVESTMENT_ANALYSER: float(len(_INVEST.findall(t))),
    }
    if _TICKER_IN_TEXT.search(t):
        scores[AgentName.INVESTMENT_ANALYSER] += 2.0
    candidates = extract_ticker_candidates(t)
    if candidates:
        scores[AgentName.INVESTMENT_ANALYSER] += 1.5 + 0.5 * min(len(candidates), 2)
    if has_investment_signal(t) and not _INVOICE.search(t):
        scores[AgentName.INVESTMENT_ANALYSER] += 0.5
    m = max(scores.values()) or 1.0
    return {k: v / m for k, v in scores.items()}


@lru_cache(maxsize=1)
def _agent_centroids() -> dict[AgentName, np.ndarray]:
    out: dict[AgentName, np.ndarray] = {}
    for agent, phrases in PROTOTYPES.items():
        emb = encode_texts(phrases)
        c = np.mean(emb, axis=0)
        n = np.linalg.norm(c) or 1.0
        out[agent] = c / n
    return out


def _embedding_vector(text: str) -> dict[AgentName, float]:
    q = encode_texts([text.strip()])[0]
    q = q / (np.linalg.norm(q) or 1.0)
    cents = _agent_centroids()
    sims = {a: float(np.dot(q, cents[a])) for a in AgentName}
    return {a: (s + 1.0) / 2.0 for a, s in sims.items()}


def classify_agent(user_message: str) -> AgentName:
    """Route to the best specialist using keywords, tickers, then embeddings."""
    t = user_message.strip()
    if not t:
        return AgentName.BUDGET_PLANNER

    kw = _keyword_vector(t)

    # Hard signals — avoid misrouting obvious cases
    invoice_hits = len(_INVOICE.findall(t))
    budget_hits = len(_BUDGET.findall(t))
    invest_hits = len(_INVEST.findall(t))
    ticker_candidates = extract_ticker_candidates(t)

    if invoice_hits >= 1 and invoice_hits >= invest_hits and invoice_hits >= budget_hits:
        return AgentName.INVOICE_GENERATOR

    if ticker_candidates or _TICKER_IN_TEXT.search(t):
        if not invoice_hits or invest_hits >= invoice_hits:
            return AgentName.INVESTMENT_ANALYSER

    if invest_hits >= 1 and invest_hits >= budget_hits and invest_hits >= invoice_hits:
        return AgentName.INVESTMENT_ANALYSER

    if budget_hits >= 1 and budget_hits > invest_hits:
        return AgentName.BUDGET_PLANNER

    emb = _embedding_vector(t)
    w = settings.intent_embedding_weight
    combined = {a: (1.0 - w) * kw[a] + w * emb[a] for a in AgentName}
    return max(combined, key=combined.get)
