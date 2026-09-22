"""Transparent heuristic confidence signals for FinMate AI responses.

This is not a calibrated probability. It summarizes execution and evidence
signals so the UI can explain why an answer is considered stronger or weaker.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.agents.types import AgentResult


def calculate_confidence(
    observations: Sequence[AgentResult],
    *,
    rag_context: str | None = None,
    failed_agents: Sequence[str] = (),
) -> dict[str, str]:
    """Return a deterministic, explainable confidence indicator."""
    if not observations:
        return {
            "confidence": "0.00",
            "confidence_level": "low",
            "confidence_method": "heuristic_v1",
            "confidence_factors": "execution=0.00,retrieval=0.00,evidence=0.00,response=0.00",
        }

    total_attempts = len(observations) + len(failed_agents)
    execution = len(observations) / total_attempts if total_attempts else 0.0

    # Presence of retrieved context is a signal that prior user context was
    # available; it is deliberately not described as a retrieval-quality score.
    retrieval = 0.90 if rag_context and rag_context.strip() else 0.65

    evidence_hits = 0
    for result in observations:
        planned = " ".join(result.planned_steps).lower()
        metadata = result.metadata
        if (
            "load_transactions" in planned
            or "portfolio" in planned
            or "invoice_ref" in metadata
            or "parsed_items_count" in metadata
            or "built from db" in result.reply.lower()
            or "actual spending" in result.reply.lower()
        ):
            evidence_hits += 1

    evidence = evidence_hits / len(observations)
    response = 1.0 if all(result.reply.strip() for result in observations) else 0.0

    score = (
        0.40 * execution
        + 0.20 * retrieval
        + 0.25 * evidence
        + 0.15 * response
    )
    score = max(0.0, min(1.0, score))

    if score >= 0.80:
        level = "high"
    elif score >= 0.60:
        level = "medium"
    else:
        level = "low"

    factors = (
        f"execution={execution:.2f},"
        f"retrieval={retrieval:.2f},"
        f"evidence={evidence:.2f},"
        f"response={response:.2f}"
    )
    return {
        "confidence": f"{score:.2f}",
        "confidence_level": level,
        "confidence_method": "heuristic_v1",
        "confidence_factors": factors,
    }
