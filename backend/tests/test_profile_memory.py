"""Regression tests for retrieval-grounded saved-profile fact lookup."""

import unittest

from app.agents.profile_memory import answer_saved_profile_fact


class ProfileMemoryTests(unittest.TestCase):
    def test_answers_saved_after_tax_income_from_retrieved_text(self):
        reply = answer_saved_profile_fact(
            "From my saved profile, what is my monthly after-tax income?",
            "The synthetic user's monthly after-tax income is INR 46500.",
        )
        self.assertIsNotNone(reply)
        self.assertIn("INR 46,500", reply)

    def test_answers_saved_risk_tolerance_from_retrieved_text(self):
        reply = answer_saved_profile_fact(
            "What investment risk tolerance did I record in my profile?",
            "The synthetic user's investment risk tolerance is conservative.",
        )
        self.assertEqual(reply, "Your saved investment risk tolerance is conservative.")

    def test_answers_saved_monthly_rent(self):
        reply = answer_saved_profile_fact(
            "What monthly rent amount did I previously tell you?",
            "The synthetic user's monthly rent is INR 12000.",
        )
        self.assertEqual(reply, "Your saved monthly rent is INR 12,000.")

    def test_answers_saved_time_horizon(self):
        reply = answer_saved_profile_fact(
            "What investment time horizon did I previously specify?",
            "The synthetic user's investment time horizon is 5 years.",
        )
        self.assertEqual(reply, "Your saved investment time horizon is 5 years.")

    def test_answers_saved_monthly_savings_goal(self):
        reply = answer_saved_profile_fact(
            "What monthly savings target did I ask you to remember?",
            "The synthetic user's monthly savings goal is INR 7000.",
        )
        self.assertEqual(reply, "Your saved monthly savings goal is INR 7,000.")

    def test_absent_memory_explicitly_abstains(self):
        reply = answer_saved_profile_fact(
            "What monthly emergency-fund contribution target did I ask you to remember? "
            "If it is not stored, say so rather than guessing.",
            "Distractor: preferred chart style is bar chart number 1. "
            "Distractor: notification window is 8:00 local time.",
        )
        self.assertIsNotNone(reply)
        self.assertIn("don't see a saved monthly emergency-fund contribution target", reply)

    def test_non_profile_advice_is_not_intercepted(self):
        self.assertIsNone(
            answer_saved_profile_fact(
                "Help me plan my monthly income and expenses.",
                "The synthetic user's monthly after-tax income is INR 46500.",
            )
        )


if __name__ == "__main__":
    unittest.main()
