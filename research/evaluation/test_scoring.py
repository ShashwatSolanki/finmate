"""Unit tests for score helpers; no live API/model required."""
import importlib.util
from pathlib import Path
import unittest
HERE=Path(__file__).resolve().parent
def load(name):
    spec=importlib.util.spec_from_file_location(name,HERE/f"{name}.py")
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
runner=load("run_benchmark"); summary=load("summarize_results")
class ScoreTests(unittest.TestCase):
    def test_grouping_number_format(self): self.assertTrue(runner.contains_number("Balance INR 30,000.00",30000,.01))
    def test_numeric_value_followed_by_comma_is_still_complete(self):
        self.assertTrue(runner.contains_number("The total is INR 3,000, based on recorded transactions.", 3000, .01))
    def test_indian_grouping_number_format(self):
        self.assertTrue(runner.contains_number("Balance INR 1,00,000.00", 100000, .01))
        self.assertTrue(runner.memory_answer_matches("Your saved income is INR 1,00,000.", "100000", .01))

    def test_malformed_grouping_is_not_scored_as_a_partial_number(self):
        self.assertFalse(runner.contains_number("Malformed INR 1,00,00", 1, .01))
        self.assertFalse(runner.contains_number("Malformed INR 1,00,00", 100000, .01))
    def test_not_partial_numeric_token(self): self.assertFalse(runner.contains_number("13000",3000,.01))
    def test_decimal_invoice_total(self): self.assertTrue(runner.contains_number("Subtotal 153.00",153,.01))
    def test_numeric_answer_requires_value_in_user_visible_reply(self):
        self.assertTrue(runner.numeric_answer_matches("The total is INR 3,000.", [3000], .01))
        self.assertFalse(runner.numeric_answer_matches("I could not determine the total.", [3000], .01))

    def test_numeric_and_memory_scoring_ignore_hidden_json(self):
        reply = 'I could not determine the total.\n\n{"gold":3000,"memory":46500}'
        self.assertFalse(runner.numeric_answer_matches(reply, [3000], .01))
        self.assertFalse(runner.memory_answer_matches(reply, "46500", .01))

    def test_abstention_scoring_ignores_hidden_json(self):
        hidden_only = 'The answer is INR 120.\n\n{"notes":"not found"}'
        visible = "I don't see that saved fact, so I won't guess."
        self.assertFalse(runner.abstention_answer_matches(hidden_only))
        self.assertTrue(runner.abstention_answer_matches(visible))


    def test_memory_numeric_answer_accepts_grouped_number_format(self):
        self.assertTrue(runner.memory_answer_matches("Your saved monthly income is INR 46,500.", "46500", .01))
        self.assertTrue(runner.memory_answer_matches("Your saved monthly savings goal is INR 8,800.", "8800", .01))

    def test_memory_numeric_answer_is_not_a_substring_match(self):
        self.assertFalse(runner.memory_answer_matches("Your saved amount is INR 146,500.", "46500", .01))

    def test_memory_text_answer_matches_case_insensitively(self):
        self.assertTrue(
            runner.memory_answer_matches(
                "Your saved investment risk tolerance is Conservative.", "conservative", .01
            )
        )

    def test_memory_risk_score_rejects_unverified_default(self):
        self.assertFalse(
            runner.memory_answer_matches(
                "I couldn't verify your saved risk tolerance. The moderate-risk split is illustrative.",
                "moderate",
                .01,
            )
        )

    def test_memory_horizon_score_requires_saved_horizon_context(self):
        self.assertTrue(
            runner.memory_answer_matches(
                "Your saved investment time horizon is 5 years.", "5 years", .01
            )
        )
        self.assertFalse(
            runner.memory_answer_matches(
                "A five-year time horizon may suit some investors.", "5 years", .01
            )
        )
    def test_invoice_payload_correct(self):
        meta={"invoice_payload":'{"currency":"INR","subtotal":"153.00","total":"153.00","line_items":[{"description":"maintenance","amount":"49.00"},{"description":"SEO","amount":"79.00"},{"description":"domain","amount":"25.00"}]}'}
        self.assertTrue(runner.invoice_payload_correct(meta,{"currency":"INR","subtotal":153,"total":153,"line_amounts":[49,79,25],"line_descriptions":["maintenance","SEO","domain"]}))
    def test_invoice_payload_wrong_subtotal(self):
        meta={"invoice_payload":'{"currency":"INR","subtotal":"152","total":"153","line_items":[{"description":"maintenance","amount":"49"},{"description":"SEO","amount":"79"},{"description":"domain","amount":"25"}]}'}
        self.assertFalse(runner.invoice_payload_correct(meta,{"currency":"INR","subtotal":153,"total":153,"line_amounts":[49,79,25],"line_descriptions":["maintenance","SEO","domain"]}))

    def test_invoice_payload_missing_total_is_rejected(self):
        meta={"invoice_payload":'{"currency":"INR","subtotal":"153","line_items":[{"description":"maintenance","amount":"49"},{"description":"SEO","amount":"79"},{"description":"domain","amount":"25"}]}'}
        self.assertFalse(runner.invoice_payload_correct(meta,{"currency":"INR","subtotal":153,"total":153,"line_amounts":[49,79,25],"line_descriptions":["maintenance","SEO","domain"]}))

    def test_invoice_payload_wrong_total_is_rejected(self):
        meta={"invoice_payload":'{"currency":"INR","subtotal":"153","total":"152","line_items":[{"description":"maintenance","amount":"49"},{"description":"SEO","amount":"79"},{"description":"domain","amount":"25"}]}'}
        self.assertFalse(runner.invoice_payload_correct(meta,{"currency":"INR","subtotal":153,"total":153,"line_amounts":[49,79,25],"line_descriptions":["maintenance","SEO","domain"]}))

    def test_invoice_payload_wrong_description_is_rejected(self):
        meta={"invoice_payload":'{"currency":"INR","subtotal":"153","total":"153","line_items":[{"description":"hosting","amount":"49"},{"description":"SEO","amount":"79"},{"description":"domain","amount":"25"}]}'}
        gold={"currency":"INR","subtotal":153,"total":153,"line_amounts":[49,79,25],"line_descriptions":["maintenance","SEO","domain"]}
        self.assertFalse(runner.invoice_payload_correct(meta,gold))
    def test_float_binary_retrieval_scores_included(self):
        score=summary.rate([{"retrieval_hit_at_5":1.0},{"retrieval_hit_at_5":0.0},{"retrieval_hit_at_5":None}],"retrieval_hit_at_5")
        self.assertEqual((score["n"],score["successes"]),(2,1))

    def test_pilot_selector_returns_21_cases_including_memory_goal(self):
        cases = []
        specifications = [
            ("numerical", "transaction_aggregation", 11),
            ("numerical", "invoice_subtotal", 6),
            ("numerical", "balance_arithmetic", 6),
            ("memory", "memory_income", 1),
            ("memory", "memory_risk", 3),
            ("memory", "memory_goal", 4),
            ("memory", "memory_absent", 5),
            ("routing", "single_specialist", 16),
            ("routing", "budget_investment", 5),
            ("routing", "budget_invoice", 6),
            ("routing", "three_domain", 5),
        ]
        for group, subgroup, count in specifications:
            for index in range(count):
                cases.append({
                    "case_id": f"{group}-{subgroup}-{index}",
                    "group": group,
                    "subgroup": subgroup,
                })

        selected = runner.select_pilot_cases(cases)
        self.assertEqual(len(selected), 21)
        self.assertTrue(any(row["subgroup"] == "memory_goal" for row in selected))
        self.assertEqual(sum(row["group"] == "numerical" for row in selected), 6)
        self.assertEqual(sum(row["group"] == "memory" for row in selected), 5)
        self.assertEqual(sum(row["group"] == "routing" for row in selected), 10)

    def test_token_must_match_selected_eval_user(self):
        from uuid import uuid4
        from app.security.jwt_tokens import create_access_token

        user_id = uuid4()
        token = create_access_token(user_id)
        self.assertTrue(runner.access_token_matches_user_id(token, str(user_id)))
        self.assertFalse(runner.access_token_matches_user_id(token, str(uuid4())))
        self.assertFalse(runner.access_token_matches_user_id("not-a-token", str(user_id)))

    def test_synthetic_eval_identity_guard(self):
        from types import SimpleNamespace
        from uuid import uuid4

        user_id = uuid4()
        synthetic = SimpleNamespace(
            email=f"research-eval-{user_id}@example.invalid",
            auth_provider="google",
            google_id=f"research-eval-{user_id}",
        )
        real_user = SimpleNamespace(
            email="owner@example.com",
            auth_provider="local",
            google_id=None,
        )
        self.assertTrue(runner.is_synthetic_eval_user(synthetic, user_id))
        self.assertFalse(runner.is_synthetic_eval_user(real_user, user_id))

    def test_dedicated_eval_database_allowed(self):
        self.assertEqual(runner.require_evaluation_database("postgresql+psycopg2://finmate:finmate@127.0.0.1:5433/finmate_eval"),"finmate_eval")

    def test_non_eval_database_rejected(self):
        with self.assertRaises(ValueError):
            runner.require_evaluation_database("postgresql+psycopg2://finmate:finmate@127.0.0.1:5433/finmate")

    def test_database_name_containing_contest_is_not_mistaken_for_test(self):
        with self.assertRaises(ValueError):
            runner.require_evaluation_database("postgresql+psycopg2://finmate:finmate@127.0.0.1:5433/finmate_contest")
if __name__=="__main__": unittest.main()
