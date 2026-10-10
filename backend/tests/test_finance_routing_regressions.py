"""Regression tests for routing budget math and saved-memory lookups before generation."""

import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.orchestrator import run_turn
from app.agents.types import AgentName, AgentResult


class FinanceRoutingRegressionTests(unittest.TestCase):
    def setUp(self):
        self.user_id = uuid4()
        self.db = MagicMock()

    def test_budget_requests_use_data_backed_budget_specialist(self):
        expected = AgentResult(
            agent=AgentName.BUDGET_PLANNER,
            reply="[AGENT: BUDGET]\nAmount remaining: INR 22500.00",
            metadata={"source": "exact_arithmetic"},
        )
        with (
            patch("app.agents.orchestrator.settings.finmate_agentic_mode", False),
            patch("app.agents.orchestrator.settings.finmate_use_llm", True),
            patch("app.agents.orchestrator.classify_agent", return_value=AgentName.BUDGET_PLANNER),
            patch("app.agents.orchestrator.budget_planner.run", return_value=expected) as budget_run,
        ):
            result = run_turn(
                self.user_id,
                "My monthly income is INR 38000, rent is INR 9000, EMI is INR 2500, "
                "groceries are INR 3000, and utilities are INR 1000. "
                "How much remains after these expenses?",
                self.db,
            )

        budget_run.assert_called_once()
        self.assertEqual(result.agent, AgentName.BUDGET_PLANNER)
        self.assertIn("22500", result.reply)

    def test_saved_profile_fact_bypasses_free_form_generation(self):
        context = "[Retrieved memory]\nThe synthetic user's monthly after-tax income is INR 46500."
        with (
            patch("app.agents.orchestrator.settings.finmate_agentic_mode", True),
            patch("app.agents.orchestrator.settings.finmate_use_llm", True),
            patch("app.agents.orchestrator.classify_agent", return_value=AgentName.BUDGET_PLANNER),
            patch("app.agents.orchestrator.run_agentic_turn") as agentic,
            patch("app.agents.orchestrator.budget_planner.run") as budget_run,
        ):
            result = run_turn(
                self.user_id,
                "From my saved profile, what is my monthly after-tax income?",
                self.db,
                rag_context=context,
            )

        agentic.assert_not_called()
        budget_run.assert_not_called()
        self.assertIn("INR 46,500", result.reply)
        self.assertEqual(result.metadata["source"], "retrieved_profile_memory")


if __name__ == "__main__":
    unittest.main()
