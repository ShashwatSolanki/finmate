import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from app.agents.agentic_orchestrator import build_plan
from app.agents.investment_analyser import _extract_original_request
from app.agents.types import AgentName


class AgenticPlannerTests(unittest.TestCase):
    def test_single_domain_request_stays_on_existing_router(self):
        self.assertIsNone(build_plan("How much did I spend on groceries?"))

    def test_budget_and_investment_request_creates_two_step_plan(self):
        plan = build_plan(
            "Analyze my spending and tell me how much I can invest this month."
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(
            [step.agent for step in plan.steps],
            [AgentName.BUDGET_PLANNER, AgentName.INVESTMENT_ANALYSER],
        )

    def test_planner_does_not_match_investigate_as_investment(self):
        self.assertIsNone(build_plan("Investigate my recent transactions."))

    def test_investment_agent_strips_agentic_observations_from_request(self):
        message = (
            "Analyze my spending and tell me how much I can invest this month."
            "\n\n[Verified specialist observations]\n"
            "[budget_planner observation]\n"
            "[AGENT: BUDGET] total: 50000"
        )
        self.assertEqual(
            _extract_original_request(message),
            "Analyze my spending and tell me how much I can invest this month.",
        )


    def test_explicit_multi_domain_request_is_not_hijacked_by_followup_agent(self):
        from app.api.routes.chat import _followup_agent_override

        db = MagicMock()
        row = MagicMock()
        row.content = "Assistant (investment_analyser): previous investment answer"
        db.scalars.return_value.all.return_value = [row]

        result = _followup_agent_override(
            db,
            uuid4(),
            "I earn 90000, spend 50000 monthly, and want to invest the remainder.",
        )
        self.assertIsNone(result)

    def test_budget_investment_invoice_plan_is_bounded(self):
        plan = build_plan(
            "Review my budget, suggest an investment, and create an invoice."
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertLessEqual(len(plan.steps), 3)
        self.assertEqual(
            [step.agent for step in plan.steps],
            [
                AgentName.BUDGET_PLANNER,
                AgentName.INVESTMENT_ANALYSER,
                AgentName.INVOICE_GENERATOR,
            ],
        )


if __name__ == "__main__":
    unittest.main()
