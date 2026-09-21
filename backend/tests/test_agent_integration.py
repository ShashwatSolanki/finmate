"""End-to-end integration coverage for the bounded agentic workflow."""

import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.agentic_orchestrator import run_agentic_turn
from app.agents.types import AgentName, AgentResult


class AgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.user_id = uuid4()

    def test_budget_investment_pipeline_passes_rag_and_observations(self):
        seen: dict[str, str] = {}

        def budget_run(user_id, message, db, rag_context=None):
            seen["budget_message"] = message
            seen["budget_rag"] = rag_context or ""
            return AgentResult(AgentName.BUDGET_PLANNER, "[AGENT: BUDGET] Investable amount: 12500 INR.")

        def investment_run(user_id, message, db, rag_context=None):
            seen["investment_message"] = message
            seen["investment_rag"] = rag_context or ""
            return AgentResult(AgentName.INVESTMENT_ANALYSER, "[AGENT: INVESTMENT] Portfolio value checked.")

        with (
            patch("app.agents.agentic_orchestrator.budget_planner.run", side_effect=budget_run),
            patch("app.agents.agentic_orchestrator.investment_analyser.run", side_effect=investment_run),
            patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", False),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending and tell me how much I can invest this month.",
                self.db,
                rag_context="salary: 50000 INR; moderate risk profile",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata["agents_executed"], "budget_planner,investment_analyser")
        self.assertEqual(seen["budget_rag"], "salary: 50000 INR; moderate risk profile")
        self.assertEqual(seen["investment_rag"], "salary: 50000 INR; moderate risk profile")
        self.assertIn("[Verified specialist observations]", seen["investment_message"])
        self.assertIn("Investable amount: 12500 INR", seen["investment_message"])

    def test_three_agent_pipeline_executes_all_specialists_and_preserves_invoice_artifacts(self):
        invoice = AgentResult(
            AgentName.INVOICE_GENERATOR,
            "[AGENT: INVOICE] Invoice generated.",
            metadata={
                "invoice_ref": "EXP-ABC123",
                "invoice_payload": '{"total":"1887"}',
                "invoice_actions": "pdf,csv",
                "parsed_items_count": "1",
                "parsed_total": "1887.00",
                "currency": "INR",
            },
        )

        with (
            patch("app.agents.agentic_orchestrator.budget_planner.run",
                  return_value=AgentResult(AgentName.BUDGET_PLANNER, "[AGENT: BUDGET] Budget checked.")),
            patch("app.agents.agentic_orchestrator.investment_analyser.run",
                  return_value=AgentResult(AgentName.INVESTMENT_ANALYSER, "[AGENT: INVESTMENT] Investment checked.")),
            patch("app.agents.agentic_orchestrator.invoice_generator.run", return_value=invoice),
            patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", False),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending, tell me how much I can invest, and generate an invoice for my expenses.",
                self.db,
                rag_context="recent expenses: 1887 INR",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata["plan_steps"], "3")
        self.assertEqual(result.metadata["agents_executed"],
                         "budget_planner,investment_analyser,invoice_generator")
        self.assertEqual(result.metadata["invoice_ref"], "EXP-ABC123")
        self.assertEqual(result.metadata["invoice_actions"], "pdf,csv")
        self.assertEqual(result.metadata["parsed_items_count"], "1")

    def test_agent_failure_does_not_discard_successful_specialist_results(self):
        with (
            patch("app.agents.agentic_orchestrator.budget_planner.run",
                  side_effect=RuntimeError("budget unavailable")),
            patch("app.agents.agentic_orchestrator.investment_analyser.run",
                  return_value=AgentResult(AgentName.INVESTMENT_ANALYSER, "[AGENT: INVESTMENT] Portfolio checked.")),
            patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", False),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending and tell me how much I can invest.",
                self.db,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.metadata["agents_executed"], "investment_analyser")
        self.assertEqual(result.metadata["agents_failed"], "budget_planner")
        self.assertIn("Portfolio checked", result.reply)

    def test_all_agent_failure_returns_no_result(self):
        with (
            patch("app.agents.agentic_orchestrator.budget_planner.run",
                  side_effect=RuntimeError("budget unavailable")),
            patch("app.agents.agentic_orchestrator.investment_analyser.run",
                  side_effect=RuntimeError("investment unavailable")),
            patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", False),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending and tell me how much I can invest.",
                self.db,
            )

        self.assertIsNone(result)

    def test_llm_synthesis_failure_uses_complete_deterministic_fallback(self):
        with (
            patch("app.agents.agentic_orchestrator.budget_planner.run",
                  return_value=AgentResult(AgentName.BUDGET_PLANNER, "[AGENT: BUDGET] Budget result.")),
            patch("app.agents.agentic_orchestrator.investment_analyser.run",
                  return_value=AgentResult(AgentName.INVESTMENT_ANALYSER, "[AGENT: INVESTMENT] Investment result.")),
            patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", True),
            patch("app.ml.finmate.llm_available", return_value=True),
            patch("app.ml.finmate.generate", side_effect=RuntimeError("generation failed")),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending and tell me how much I can invest.",
                self.db,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("Budget Planner", result.reply)
        self.assertIn("Investment Analyser", result.reply)
        self.assertIn("deterministic synthesis fallback", result.reply)

    def test_synthesis_rejects_partial_llm_output_and_preserves_all_agents(self):
        with (
            patch("app.agents.agentic_orchestrator.budget_planner.run",
                  return_value=AgentResult(AgentName.BUDGET_PLANNER, "[AGENT: BUDGET] Budget result.")),
            patch("app.agents.agentic_orchestrator.investment_analyser.run",
                  return_value=AgentResult(AgentName.INVESTMENT_ANALYSER, "[AGENT: INVESTMENT] Investment result.")),
            patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", True),
            patch("app.ml.finmate.llm_available", return_value=True),
            patch("app.ml.finmate.generate", return_value="[AGENT: BUDGET]\nOnly budget answer."),
            patch("app.ml.finmate.finalize_llm_reply", side_effect=lambda value: value),
        ):
            result = run_agentic_turn(
                self.user_id,
                "Analyze my spending and tell me how much I can invest.",
                self.db,
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("Budget Planner", result.reply)
        self.assertIn("Investment Analyser", result.reply)
        self.assertIn("deterministic synthesis fallback", result.reply)

    def test_single_domain_request_does_not_enter_agentic_pipeline(self):
        with patch("app.agents.agentic_orchestrator.budget_planner.run") as budget_run:
            result = run_agentic_turn(self.user_id, "How much did I spend on food?", self.db)

        self.assertIsNone(result)
        budget_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
