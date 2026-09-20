"""Small deterministic RAG evaluation harness for FinMate.

This script evaluates retrieval against a labeled fixture rather than the live
database, making the metric reproducible on a developer machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag import memory_store


@dataclass(frozen=True)
class Case:
    query: str
    relevant: tuple[str, ...]


CASES = (
    Case(
        "How much did I spend on food?",
        ("Food spending was 6897 INR", "Food budget is 8000 INR"),
    ),
    Case(
        "What is my investment risk tolerance?",
        ("Risk tolerance is moderate",),
    ),
    Case(
        "What is my monthly income?",
        ("Monthly income is 50000 INR",),
    ),
)


def run_fixture_evaluation() -> dict[str, float]:
    passed = 0
    reciprocal_ranks: list[float] = []

    original = memory_store.encode_texts

    try:
        def encode_fixture(texts: list[str]):
            vectors = []
            for text in texts:
                t = text.lower()
                if "food" in t:
                    vectors.append([1.0, 0.0, 0.0])
                elif "risk" in t or "investment" in t:
                    vectors.append([0.0, 1.0, 0.0])
                elif "income" in t:
                    vectors.append([0.0, 0.0, 1.0])
                else:
                    vectors.append([0.01, 0.01, 0.01])
            return vectors

        memory_store.encode_texts = encode_fixture

        class Row:
            def __init__(self, content: str):
                self.content = content

        class Scalars:
            def __init__(self, rows):
                self.rows = rows

            def all(self):
                return self.rows

        class DB:
            def __init__(self, rows):
                self.rows = rows

            def scalars(self, _query):
                return Scalars(self.rows)

        rows = [
            Row("Monthly income is 50000 INR"),
            Row("Risk tolerance is moderate"),
            Row("Food budget is 8000 INR"),
            Row("Food spending was 6897 INR"),
        ]
        from uuid import uuid4

        for case in CASES:
            ranked = memory_store.rank_memory(DB(rows), uuid4(), case.query, limit=2, min_similarity=0.2)
            retrieved = [item for item, _score in ranked]
            rank = next((i + 1 for i, item in enumerate(retrieved) if item in case.relevant), None)
            if rank is not None:
                passed += 1
                reciprocal_ranks.append(1.0 / rank)
            else:
                reciprocal_ranks.append(0.0)

        count = len(CASES)
        return {
            "cases": float(count),
            "hit_rate": passed / count,
            "mrr": sum(reciprocal_ranks) / count,
        }
    finally:
        memory_store.encode_texts = original


if __name__ == "__main__":
    metrics = run_fixture_evaluation()
    print(f"RAG cases: {int(metrics['cases'])}")
    print(f"Hit@2: {metrics['hit_rate']:.2%}")
    print(f"MRR: {metrics['mrr']:.3f}")
