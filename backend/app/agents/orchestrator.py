"""Central orchestration: optional FinMate LLM, else rule-based specialist agents."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents import budget_planner, invoice_generator, investment_analyser
from app.agents.confidence import calculate_confidence
from app.agents.agentic_orchestrator import run_agentic_turn
from app.agents.intent import classify_agent
from app.agents.profile_memory import answer_saved_profile_fact
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
    Route first to data-backed specialists. The budget specialist owns
    transaction aggregation and exact arithmetic; the general LLM must not
    bypass those verified paths for finance questions.
    """
    chosen: AgentName | None = agent

    def _finalize(result: AgentResult) -> AgentResult:
        result.metadata.update(calculate_confidence([result], rag_context=rag_context))
        return result

    # Explicit saved-profile questions are answered only from retrieved context.
    # Missing facts are stated as missing instead of sending an invitation to guess
    # to a generative model.
    profile_reply = answer_saved_profile_fact(user_message, rag_context)
    if profile_reply is not None:
        profile_agent = chosen or classify_agent(user_message)
        return _finalize(
            AgentResult(
                agent=profile_agent,
                reply=(
                    "[AGENT: INVOICE]\n\n" + profile_reply
                    if profile_agent == AgentName.INVOICE_GENERATOR
                    else "[AGENT: INVESTMENT]\n\n" + profile_reply
                    if profile_agent == AgentName.INVESTMENT_ANALYSER
                    else "[AGENT: BUDGET]\n\n" + profile_reply
                ),
                planned_steps=["retrieve_profile_memory", "match_requested_fact", "answer_or_abstain"],
                metadata={"source": "retrieved_profile_memory"},
            )
        )

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
            return _finalize(agentic_result)

    if chosen is None:
        classified = classify_agent(user_message)
        if classified in (
            AgentName.BUDGET_PLANNER,
            AgentName.INVOICE_GENERATOR,
            AgentName.INVESTMENT_ANALYSER,
        ):
            chosen = classified

    # A general-model-only path is retained for any future unclassified agent
    # state, but classified budget requests now go through budget_planner.run().
    # That specialist can enforce exact numeric results and still use the LLM
    # to explain the verified data for ordinary budget advice.
    if settings.finmate_use_llm and chosen is None:
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
                return _finalize(
                    AgentResult(
                        agent=agent_enum,
                        reply=reply,
                        planned_steps=steps or ["parse_intent", "respond"],
                        metadata=meta,
                    )
                )
        except Exception as e:
            logger.exception("FinMate LLM failed; falling back to rule-based agents: %s", e)

    if chosen is None:
        chosen = classify_agent(user_message)

    if chosen == AgentName.BUDGET_PLANNER:
        res = budget_planner.run(user_id, user_message, db, rag_context=rag_context)
        res.metadata = {**res.metadata, "source": res.metadata.get("source", "rules")}
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
    res.metadata = {**res.metadata, "source": res.metadata.get("source", "rules")}
    return _finalize(res)
