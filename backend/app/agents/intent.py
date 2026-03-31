"""Hybrid intent: fast regex keywords + embedding similarity to agent prototypes (open MiniLM)."""

from __future__ import annotations

import re
from functools import lru_cache

import numpy as np

from app.agents.types import AgentName
from app.config import settings
from app.ml.embeddings import encode_texts

# Keyword layer (fast path)
_BUDGET = re.compile(
    r"\b(budget|spend|spending|expense|save money|cut cost|monthly|category|overspend|saving|savings|groceries|rent|utilities|bills|income|outgoings)\b",
    re.I,
)
_INVOICE = re.compile(
    r"\b(invoice|bill|receipt|pdf bill|generate invoice|itemize|line items|billing|client invoice|net 30|net 15|payment terms|due on receipt|freelance invoice)\b",
    re.I,
)
_INVEST = re.compile(
    r"\b(stock|ticker|portfolio|invest|investing|investment|equity|nasdaq|nyse|quote|dividend|pe ratio|volatility|sma|chart|should I buy|buy now|AAPL|MSFT|TSLA|GOOGL|AMZN|NVDA|market|shares|ETF|index fund|mutual fund|crypto|bitcoin)\b",
    re.I,
)

# Prototype phrases per agent — embedding layer matches user text to these regions
PROTOTYPES: dict[AgentName, list[str]] = {
    AgentName.BUDGET_PLANNER: [
        "Help me reduce my monthly spending and stick to a budget.",
        "How much did I spend on food and dining last month?",
        "I need a plan to save money and track expenses by category.",
        "Compare my spending this month to last month.",
        "My rent is too high, how do I manage my expenses?",
        "I am overspending on groceries every month.",
    ],
    AgentName.INVOICE_GENERATOR: [
        "Generate a PDF invoice for my freelance client with line items.",
        "Create a bill with amounts and descriptions for services rendered.",
        "I need an itemized invoice template for accounting.",
        "Make a professional invoice for web development work.",
        "I need to bill my client for consulting services.",
    ],
    AgentName.INVESTMENT_ANALYSER: [
        "Analyze AAPL stock price trend and moving averages.",
        "What is the latest quote and historical volatility for MSFT?",
        "Should I look at portfolio risk and equity exposure?",
        "Should I invest in AAPL right now?",
        "Is TSLA a good buy this week?",
        "I have 10000 to invest for 5 years, what should I do?",
        "What stocks should I buy right now?",
        "Analyze the market conditions for investing.",
    ],
}


def _keyword_vector(text: str) -> dict[AgentName, float]:
    t = text.strip()
    scores = {
        AgentName.BUDGET_PLANNER: float(len(_BUDGET.findall(t))),
        AgentName.INVOICE_GENERATOR: float(len(_INVOICE.findall(t))),
        AgentName.INVESTMENT_ANALYSER: float(len(_INVEST.findall(t))),
    }
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
    # shift from [-1,1] to [0,1] for blending
    return {a: (s + 1.0) / 2.0 for a, s in sims.items()}


def classify_agent(user_message: str) -> AgentName:
    """Hybrid router: combine normalized keyword scores with embedding similarity."""
    kw = _keyword_vector(user_message)
    emb = _embedding_vector(user_message)
    w = settings.intent_embedding_weight
    combined = {a: (1.0 - w) * kw[a] + w * emb[a] for a in AgentName}
    return max(combined, key=combined.get)