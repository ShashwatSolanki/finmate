"""Central orchestration: optional FinMate LLM, else rule-based specialist agents."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents import budget_planner, invoice_generator, investment_analyser
from app.agents.intent import classify_agent
from app.agents.types import AgentName, AgentResult
from app.config import settings

logger = logging.getLogger(__name__)

_ROUTE_TO_AGENT = {
    "budget_planner": AgentName.BUDGET_PLANNER,
    "invoice_generator": AgentName.INVOICE_GENERATOR,
    "investment_analyser": AgentName.INVESTMENT_ANALYSER,
}


def _compose_llm_user_message(user_message: str, rag_context: str | None) -> str:
    if rag_context and rag_context.strip():
        return (
            "Context from retrieved memory (may help answer):\n"
            f"{rag_context.strip()[:6000]}\n\n"
            "---\n\n"
            f"User message:\n{user_message.strip()}"
        )
    return user_message.strip()


def run_turn(
    user_id: UUID,
    user_message: str,
    db: Session,
    agent: AgentName | None = None,
    rag_context: str | None = None,
) -> AgentResult:
    """
    If `settings.finmate_use_llm` and LoRA weights exist, run the local model once
    (same format as training: tag + prose + JSON tail). Otherwise route to rule-based agents.
    Forced `agent` skips the LLM and uses the matching specialist (DB/tools/yfinance).
    """
    chosen: AgentName | None = agent
    # Skip embedding-based intent when we may handle the turn with the local LLM (saves loading MiniLM).
    if chosen is None and not (settings.finmate_use_llm and agent is None):
        chosen = classify_agent(user_message)

    if settings.finmate_use_llm and agent is None:
        try:
            from app.ml import finmate

            if not finmate.llm_available():
                logger.warning("FINMATE_USE_LLM is on but no adapter weights found; using rule-based agents.")
            else:
                prompt = _compose_llm_user_message(user_message, rag_context)
                reply = finmate.finalize_llm_reply(finmate.generate(prompt))
                route = finmate.route_key_from_reply(reply)
                agent_enum = _ROUTE_TO_AGENT.get(route, AgentName.BUDGET_PLANNER)
                steps = finmate.extract_planned_steps(reply)
                meta = {"source": "llm", "route_key": route}
                if rag_context and rag_context.strip():
                    meta["rag_injected"] = "true"
                return AgentResult(
                    agent=agent_enum,
                    reply=reply,
                    planned_steps=steps or ["parse_intent", "respond"],
                    metadata=meta,
                )
        except Exception as e:
            logger.exception("FinMate LLM failed; falling back to rule-based agents: %s", e)

    if chosen is None:
        chosen = classify_agent(user_message)

    if chosen == AgentName.BUDGET_PLANNER:
        res = budget_planner.run(user_id, user_message, db, rag_context=rag_context)
        res.metadata = {**res.metadata, "source": "rules"}
        return res
    if chosen == AgentName.INVOICE_GENERATOR:
        res = invoice_generator.run(user_id, user_message, db, rag_context=rag_context)
        res.metadata = {**res.metadata, "source": "rules"}
        return res
    if chosen == AgentName.INVESTMENT_ANALYSER:
        res = investment_analyser.run(user_id, user_message, db, rag_context=rag_context)
        res.metadata = {**res.metadata, "source": "rules"}
        return res

    res = budget_planner.run(user_id, user_message, db, rag_context=rag_context)
    res.metadata = {**res.metadata, "source": "rules"}
    return res
