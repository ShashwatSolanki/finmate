"""Tests for the transparent confidence indicator."""

import unittest

from app.agents.confidence import calculate_confidence
from app.agents.types import AgentName, AgentResult


class ConfidenceTests(unittest.TestCase):
    def test_grounded_success_with_rag_is_high(self):
        result = AgentResult(
            AgentName.BUDGET_PLANNER,
            "[AGENT: BUDGET]\nHere is your actual spending picture.",
            planned_steps=["load_transactions_30d", "aggregate_by_category"],
        )
        confidence = calculate_confidence(
            [result],
            rag_context="monthly income: 50000 INR",
        )
        self.assertEqual(confidence["confidence_level"], "high")
        self.assertEqual(confidence["confidence_method"], "heuristic_v1")
        self.assertIn("execution=1.00", confidence["confidence_factors"])

    def test_failed_agent_reduces_confidence(self):
        result = AgentResult(
            AgentName.INVESTMENT_ANALYSER,
            "[AGENT: INVESTMENT] Portfolio checked.",
            planned_steps=["load_portfolio"],
        )
        confidence = calculate_confidence(
            [result],
            failed_agents=["budget_planner"],
        )
        self.assertLess(float(confidence["confidence"]), 0.80)
        self.assertIn("execution=0.50", confidence["confidence_factors"])

    def test_empty_observations_are_low_confidence(self):
        confidence = calculate_confidence([], failed_agents=["budget_planner"])
        self.assertEqual(confidence["confidence"], "0.00")
        self.assertEqual(confidence["confidence_level"], "low")


if __name__ == "__main__":
    unittest.main()
