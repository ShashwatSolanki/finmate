import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.agentic_orchestrator import _synthesize, build_plan
from app.agents.investment_analyser import _extract_original_request
from app.agents.types import AgentName, AgentResult


class AgenticPlannerTests(unittest.TestCase):
    def test_deterministic_synthesis_skips_model_and_preserves_observations(self):
        observations = [
            AgentResult(
                agent=AgentName.BUDGET_PLANNER,
                reply="[AGENT: BUDGET]\nVerified budget observation",
                planned_steps=[],
                metadata={},
            ),
            AgentResult(
                agent=AgentName.INVESTMENT_ANALYSER,
                reply="[AGENT: INVESTMENT]\nVerified investment observation",
                planned_steps=[],
                metadata={},
            ),
        ]
        from app.ml import finmate

        with patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", True), patch(
            "app.agents.agentic_orchestrator.settings.finmate_agentic_synthesis", False
        ), patch.object(finmate, "generate") as generate:
            reply = _synthesize(
                "Review my budget and suggest investments.",
                observations,
                AgentName.BUDGET_PLANNER,
            )

        generate.assert_not_called()
        self.assertIn("Verified budget observation", reply)
        self.assertIn("Verified investment observation", reply)
        self.assertIn("deterministic synthesis fallback", reply)

    def test_synthesis_prompt_matches_all_specialist_tag_validation(self):
        from app.ml import finmate

        observations = [
            AgentResult(
                agent=AgentName.BUDGET_PLANNER,
                reply="[AGENT: BUDGET]\nVerified budget observation",
                planned_steps=[],
                metadata={},
            ),
            AgentResult(
                agent=AgentName.INVESTMENT_ANALYSER,
                reply="[AGENT: INVESTMENT]\nIllustrative investment allocation",
                planned_steps=[],
                metadata={},
            ),
        ]
        synthesized = (
            "[AGENT: BUDGET]\n\n"
            "Budget summary: no transactions are available.\n\n"
            "[AGENT: INVESTMENT]\n"
            "The moderate-risk allocation is illustrative, not personalized.\n\n"
            '{"intent":"multi_agent_finance_task","steps":["Review budget","Review allocation"],'
            '"tools_needed":["specialist_agents"],"notes":"synthesized verified observations"}'
        )

        with (
            patch("app.agents.agentic_orchestrator.settings.finmate_use_llm", True),
            patch("app.agents.agentic_orchestrator.settings.finmate_agentic_synthesis", True),
            patch.object(finmate, "llm_available", return_value=True),
            patch.object(finmate, "generate", return_value=synthesized) as generate,
        ):
            reply = _synthesize(
                "Review my budget and suggest investments.",
                observations,
                AgentName.BUDGET_PLANNER,
            )

        prompt = generate.call_args.args[0]
        self.assertEqual(generate.call_args.kwargs["max_new_tokens"], 256)
        self.assertIn("clearly labelled section for every specialist", prompt)
        self.assertIn("[AGENT: INVESTMENT]", reply)
        self.assertIn("illustrative, not personalized", reply)
        self.assertNotIn("deterministic synthesis fallback", reply)

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

    def test_investable_surplus_triggers_investment_specialist(self):
        plan = build_plan(
            "Review my budget, estimate investable surplus, and invoice a client for development INR 3600."
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(
            [step.agent for step in plan.steps],
            [
                AgentName.BUDGET_PLANNER,
                AgentName.INVESTMENT_ANALYSER,
                AgentName.INVOICE_GENERATOR,
            ],
        )

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
