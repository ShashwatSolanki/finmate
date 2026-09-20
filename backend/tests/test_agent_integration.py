"""Integration tests for the bounded agentic RAG-to-agent pipeline."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.agentic_orchestrator import run_agentic_turn
from app.agents.types import AgentName, AgentResult


def _result(agent: AgentName, reply: str) -> AgentResult:
    return AgentResult(agent=agent, reply=reply, planned_steps=["respond"])


class AgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.user_id = uuid4()

    def test_single_domain_never_enters_agentic_execution(self):
        with patch("app.agents.agentic_orchestrator.budget_planner.run") as budget:
            result = run_agentic_turn(
                self.user_id,
                "Show me my spending this month.",
                self.db,
            )
        self.assertIsNone(result)
        budget.assert_not_called()

    def test_two_agent_pipeline_executes_in_plan_order_and_passes_observation(self):
        budget_result = _result(
            AgentName.BUDGET_PLANNER,
            "[AGENT: BUDGET] Available cash: 12000 INR.",
        )
        investment_result = _result(
            AgentName.INVESTMENT_ANALYSER,
            "[AGENT: INVESTMENT] Investment context received.",
        )

        with (
            patch(
                "app.agents.agentic_orchestrator.budget_planner.run",
                return_value=budget_result,
            ) as budget,
            patch(
                "app.agents.agentic_orchestrator.investment_analyser.run",
                return_value=investment_result,
            ) as investment,
            patch(
                "app.agents.agentic_orchestrator.settings.finmate_use_llm",
                False,
            ),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending and tell me how much I can invest.",
                self.db,
                rag_context="[Retrieved memory]\nIncome is 50000 INR.",
            )

        self.assertIsNotNone(result)
        assert result is not None
        budget.assert_called_once()
        investment.assert_called_once()

        investment_message = investment.call_args.args[1]
        self.assertIn("[Verified specialist observations]", investment_message)
        self.assertIn("Available cash: 12000 INR", investment_message)
        self.assertEqual(
            result.metadata["agents_executed"],
            "budget_planner,investment_analyser",
        )
        self.assertEqual(result.metadata["plan_steps"], "2")

    def test_three_agent_pipeline_executes_all_specialists(self):
        results = {
            AgentName.BUDGET_PLANNER: _result(
                AgentName.BUDGET_PLANNER, "[AGENT: BUDGET] budget"
            ),
            AgentName.INVESTMENT_ANALYSER: _result(
                AgentName.INVESTMENT_ANALYSER, "[AGENT: INVESTMENT] investment"
            ),
            AgentName.INVOICE_GENERATOR: _result(
                AgentName.INVOICE_GENERATOR, "[AGENT: INVOICE] invoice"
            ),
        }

        with (
            patch(
                "app.agents.agentic_orchestrator.budget_planner.run",
                return_value=results[AgentName.BUDGET_PLANNER],
            ) as budget,
            patch(
                "app.agents.agentic_orchestrator.investment_analyser.run",
                return_value=results[AgentName.INVESTMENT_ANALYSER],
            ) as investment,
            patch(
                "app.agents.agentic_orchestrator.invoice_generator.run",
                return_value=results[AgentName.INVOICE_GENERATOR],
            ) as invoice,
            patch(
                "app.agents.agentic_orchestrator.settings.finmate_use_llm",
                False,
            ),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending, tell me how much I can invest, and generate an invoice.",
                self.db,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata["plan_steps"], "3")
        self.assertEqual(
            result.metadata["planned_agents"],
            "budget_planner,investment_analyser,invoice_generator",
        )
        self.assertEqual(
            result.metadata["agents_executed"],
            "budget_planner,investment_analyser,invoice_generator",
        )
        budget.assert_called_once()
        investment.assert_called_once()
        invoice.assert_called_once()

    def test_failed_specialist_does_not_discard_successful_agents(self):
        investment_result = _result(
            AgentName.INVESTMENT_ANALYSER,
            "[AGENT: INVESTMENT] fallback investment result",
        )

        with (
            patch(
                "app.agents.agentic_orchestrator.budget_planner.run",
                side_effect=RuntimeError("budget unavailable"),
            ),
            patch(
                "app.agents.agentic_orchestrator.investment_analyser.run",
                return_value=investment_result,
            ),
            patch(
                "app.agents.agentic_orchestrator.settings.finmate_use_llm",
                False,
            ),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending and tell me how much I can invest.",
                self.db,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata["agents_failed"], "budget_planner")
        self.assertEqual(result.metadata["agents_executed"], "investment_analyser")
        self.assertEqual(result.metadata["plan_steps"], "1")

    def test_all_specialists_failing_returns_none(self):
        with (
            patch(
                "app.agents.agentic_orchestrator.budget_planner.run",
                side_effect=RuntimeError("budget unavailable"),
            ),
            patch(
                "app.agents.agentic_orchestrator.investment_analyser.run",
                side_effect=RuntimeError("investment unavailable"),
            ),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending and tell me how much I can invest.",
                self.db,
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
