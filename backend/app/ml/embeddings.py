"""Lazy-loaded sentence embeddings for hybrid intent routing (open model, runs locally)."""

from __future__ import annotations

from functools import lru_cache
import os

from app.config import settings


@lru_cache(maxsize=1)
def get_sentence_model():
    # FinMate uses the PyTorch embedding path. Prevent Transformers from
    # probing the TensorFlow/Keras stack on Windows during model import.
    os.environ.setdefault("USE_TF", "0")
    os.environ.setdefault("USE_TORCH", "1")

    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model_name, backend="torch")


def encode_texts(texts: list[str]):
    model = get_sentence_model()
    return model.encode(texts, normalize_embeddings=True)
