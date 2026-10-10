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
    def test_not_partial_numeric_token(self): self.assertFalse(runner.contains_number("13000",3000,.01))
    def test_decimal_invoice_total(self): self.assertTrue(runner.contains_number("Subtotal 153.00",153,.01))
    def test_invoice_payload_correct(self):
        meta={"invoice_payload":'{"currency":"INR","subtotal":"153.00","line_items":[{"amount":"49.00"},{"amount":"79.00"},{"amount":"25.00"}]}'}
        self.assertTrue(runner.invoice_payload_correct(meta,{"currency":"INR","subtotal":153,"line_amounts":[49,79,25]}))
    def test_invoice_payload_wrong_subtotal(self):
        meta={"invoice_payload":'{"currency":"INR","subtotal":"152","line_items":[{"amount":"49"},{"amount":"79"},{"amount":"25"}]}'}
        self.assertFalse(runner.invoice_payload_correct(meta,{"currency":"INR","subtotal":153,"line_amounts":[49,79,25]}))
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

    def test_dedicated_eval_database_allowed(self):
        self.assertEqual(runner.require_evaluation_database("postgresql+psycopg2://finmate:finmate@127.0.0.1:5433/finmate_eval"),"finmate_eval")

    def test_non_eval_database_rejected(self):
        with self.assertRaises(ValueError):
            runner.require_evaluation_database("postgresql+psycopg2://finmate:finmate@127.0.0.1:5433/finmate")
if __name__=="__main__": unittest.main()
