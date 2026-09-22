"""Central orchestration: optional FinMate LLM, else rule-based specialist agents."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents import budget_planner, invoice_generator, investment_analyser
from app.agents.confidence import calculate_confidence
from app.agents.agentic_orchestrator import run_agentic_turn
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
    A forced Budget selection still uses the trained model; other forced specialists
    retain their deterministic/tool-backed flows.
    """
    chosen: AgentName | None = agent

    def _finalize(result: AgentResult) -> AgentResult:
        result.metadata.update(calculate_confidence([result], rag_context=rag_context))
        return result

    # Use the bounded planner only for requests that clearly span multiple
    # specialists. Single-domain requests keep the existing routing path.
    if chosen is None and settings.finmate_agentic_mode:
        agentic_result = run_agentic_turn(
            user_id,
            user_message,
            db,
            rag_context=rag_context,
        )
        if agentic_result is not None:
            return agentic_result
    # Structured specialist flows should run before the general model so
    # tool-backed data is authoritative and exportable artifacts survive.
    if chosen is None:
        classified = classify_agent(user_message)
        if classified in (
            AgentName.BUDGET_PLANNER,
            AgentName.INVOICE_GENERATOR,
            AgentName.INVESTMENT_ANALYSER,
        ):
            chosen = classified
    # Skip embedding-based intent for the remaining budget/general-chat path.
    if chosen is None and not (settings.finmate_use_llm and agent is None):
        chosen = classify_agent(user_message)

    if settings.finmate_use_llm and chosen in (None, AgentName.BUDGET_PLANNER):
        try:
            from app.ml import finmate

            if not finmate.llm_available():
                logger.warning("FINMATE_USE_LLM is on but no adapter weights found; using rule-based agents.")
            else:
                budget_instruction = (
                    "\n\nThis is the BUDGET specialist flow. Start with exactly [AGENT: BUDGET] "
                    "and give a concrete, personalized budget response."
                    if agent == AgentName.BUDGET_PLANNER
                    else ""
                )
                prompt = _compose_llm_user_message(user_message, rag_context) + budget_instruction
                reply = finmate.finalize_llm_reply(finmate.generate(prompt))
                route = finmate.route_key_from_reply(reply)
                # Preserve the deterministic/explicit specialist selection.
                # The model generates the response, but must not override the
                # already-selected route (especially for budget queries).
                agent_enum = chosen or _ROUTE_TO_AGENT.get(route, AgentName.BUDGET_PLANNER)
                steps = finmate.extract_planned_steps(reply)
                meta = {"source": "llm", "route_key": route}
                if rag_context and rag_context.strip():
                    meta["rag_injected"] = "true"
                return _finalize(AgentResult(
                    agent=agent_enum,
                    reply=reply,
                    planned_steps=steps or ["parse_intent", "respond"],
                    metadata=meta,
                ))
        except Exception as e:
            logger.exception("FinMate LLM failed; falling back to rule-based agents: %s", e)

    if chosen is None:
        chosen = classify_agent(user_message)

    if chosen == AgentName.BUDGET_PLANNER:
        res = budget_planner.run(user_id, user_message, db, rag_context=rag_context)
        res.metadata = {**res.metadata, "source": "rules"}
        return _finalize(res)
    if chosen == AgentName.INVOICE_GENERATOR:
        res = invoice_generator.run(user_id, user_message, db, rag_context=rag_context)
        res.metadata = {**res.metadata, "source": "rules"}
        return _finalize(res)
    if chosen == AgentName.INVESTMENT_ANALYSER:
        res = investment_analyser.run(user_id, user_message, db, rag_context=rag_context)
        res.metadata = {**res.metadata, "source": "rules"}
        return _finalize(res)

    res = budget_planner.run(user_id, user_message, db, rag_context=rag_context)
    res.metadata = {**res.metadata, "source": "rules"}
    return _finalize(res)
