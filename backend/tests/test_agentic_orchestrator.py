import unittest

from app.agents.agentic_orchestrator import build_plan
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
