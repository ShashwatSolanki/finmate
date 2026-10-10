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
if __name__=="__main__": unittest.main()
