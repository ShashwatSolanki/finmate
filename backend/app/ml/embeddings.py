"""Lazy-loaded sentence embeddings for hybrid intent routing (open model, runs locally)."""

from __future__ import annotations

from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def get_sentence_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model_name)


def encode_texts(texts: list[str]):
    model = get_sentence_model()
    return model.encode(texts, normalize_embeddings=True)
