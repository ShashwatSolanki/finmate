"""Structural checks for the synthetic benchmark dataset after generation."""
import json
from collections import Counter
from pathlib import Path
import unittest
HERE=Path(__file__).resolve().parent
class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=HERE/"benchmark.jsonl"
        if not path.exists():
            import subprocess,sys
            subprocess.run([sys.executable,str(HERE/"generate_benchmark.py")],check=True)
        cls.rows=[json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    def test_120_case_balance(self):
        self.assertEqual(len(self.rows),120)
        self.assertEqual(len({r["case_id"] for r in self.rows}),120)
        self.assertEqual(Counter(r["group"] for r in self.rows),{"numerical":40,"memory":40,"routing":40})
    def test_invoice_gold_totals(self):
        rows=[r for r in self.rows if r.get("gold_invoice")]
        self.assertEqual(len(rows),10)
        for r in rows:
            g=r["gold_invoice"]
            self.assertEqual(sum(g["line_amounts"]),g["subtotal"])
            self.assertEqual(g["subtotal"],r["gold_numeric_values"][0])
    def test_transaction_gold_values(self):
        rows=[r for r in self.rows if r.get("subgroup")=="transaction_aggregation"]
        for r in rows:
            recent=sum(x["amount"] for x in r["transactions_fixture"] if x["category"]=="food" and x["days_ago"]<=30)
            self.assertEqual(recent,r["gold_numeric_values"][0])
    def test_absent_memory_abstention(self):
        rows=[r for r in self.rows if r.get("expected_abstention")]
        self.assertEqual(len(rows),8)
        for r in rows:
            self.assertIsNone(r["relevant_memory"])
            self.assertIsNone(r["gold_memory_answer"])
    def test_specialist_labels(self):
        agents={"budget_planner","investment_analyser","invoice_generator"}
        for r in self.rows:
            if r.get("expected_agent"): self.assertIn(r["expected_agent"],agents)
            self.assertLessEqual(len(r.get("expected_agents",[])),3)
if __name__=="__main__": unittest.main()
