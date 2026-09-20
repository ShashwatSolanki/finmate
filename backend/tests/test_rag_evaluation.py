"""Unit tests for FinMate RAG retrieval and context construction."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

from app.api.routes.chat import (
    _build_recent_context,
    _latest_onboarding_context,
)
from app.rag import memory_store


class _FakeRow:
    def __init__(self, content: str, source: str = "chat"):
        self.content = content
        self.source = source


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _query):
        return _FakeScalarResult(self.rows)

    def scalar(self, _query):
        return self.rows[0] if self.rows else None


class RAGRetrievalTests(unittest.TestCase):
    def test_empty_query_returns_no_results(self):
        db = _FakeDB([])
        self.assertEqual(memory_store.search_memory(db, uuid4(), "   "), [])

    def test_empty_memory_returns_no_results(self):
        db = _FakeDB([])
        with patch.object(memory_store, "encode_texts") as encode:
            self.assertEqual(memory_store.search_memory(db, uuid4(), "food spending"), [])
            encode.assert_not_called()

    def test_retrieval_orders_by_cosine_similarity_and_respects_k(self):
        rows = [
            _FakeRow("irrelevant investment note"),
            _FakeRow("food spending was high"),
            _FakeRow("food budget is 8000"),
        ]
        # Query vector + three document vectors.
        embeddings = [
            [1.0, 0.0],
            [0.1, 0.0],
            [0.9, 0.0],
            [0.8, 0.0],
        ]
        db = _FakeDB(rows)
        with patch.object(memory_store, "encode_texts", return_value=embeddings):
            result = memory_store.search_memory(db, uuid4(), "food spending", k=2, min_similarity=0.5)
        self.assertEqual(
            result,
            ["food spending was high", "food budget is 8000"],
        )

    def test_low_similarity_chunks_are_filtered(self):
        rows = [_FakeRow("weak match"), _FakeRow("strong match")]
        embeddings = [[1.0, 0.0], [0.2, 0.0], [0.9, 0.0]]
        db = _FakeDB(rows)
        with patch.object(memory_store, "encode_texts", return_value=embeddings):
            result = memory_store.search_memory(db, uuid4(), "query", k=5, min_similarity=0.5)
        self.assertEqual(result, ["strong match"])

    def test_embedding_failure_falls_back_to_recent_chunks(self):
        rows = [
            _FakeRow("newest", "chat"),
            _FakeRow("second newest", "chat"),
            _FakeRow("oldest", "chat"),
        ]
        db = _FakeDB(rows)
        with patch.object(memory_store, "encode_texts", side_effect=RuntimeError("embedding unavailable")):
            result = memory_store.search_memory(db, uuid4(), "query", k=2)
        self.assertEqual(result, ["newest", "second newest"])


class ContextBuilderTests(unittest.TestCase):
    def test_recent_context_preserves_chronological_order(self):
        rows = [
            _FakeRow("Assistant response 2"),
            _FakeRow("User query 2"),
            _FakeRow("Assistant response 1"),
            _FakeRow("User query 1"),
        ]
        db = _FakeDB(rows)
        self.assertEqual(
            _build_recent_context(db, uuid4(), turns=2),
            "Assistant response 1\nUser query 1\nAssistant response 2\nUser query 2",
        )

    def test_recent_context_is_capped(self):
        rows = [_FakeRow(f"chunk-{i}") for i in range(20)]
        context = _build_recent_context(db := _FakeDB(rows), uuid4(), turns=3)
        self.assertIsNotNone(context)
        assert context is not None
        self.assertLessEqual(len(context), 3000)

    def test_latest_onboarding_context_returns_latest_profile(self):
        row = _FakeRow("risk tolerance: moderate\nmonthly income: 50000", "onboarding")
        db = _FakeDB([row])
        self.assertEqual(
            _latest_onboarding_context(db, uuid4()),
            "risk tolerance: moderate\nmonthly income: 50000",
        )


if __name__ == "__main__":
    unittest.main()
