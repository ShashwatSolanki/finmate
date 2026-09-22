"""RAG layer: MemoryChunk in Postgres + embedding similarity (no native Chroma build on Windows)."""

from __future__ import annotations

import logging
from uuid import UUID

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MemoryChunk
from app.ml.embeddings import encode_texts

logger = logging.getLogger(__name__)


def rank_memory(
    db: Session,
    user_id: UUID,
    query: str,
    limit: int = 5,
    min_similarity: float = 0.22,
) -> list[tuple[str, float]]:
    """Return ranked memory text and cosine score without changing search_memory's API."""
    q = query.strip()
    if not q or limit <= 0:
        return []

    rows = db.scalars(
        select(MemoryChunk)
        .where(MemoryChunk.user_id == user_id)
        .order_by(MemoryChunk.created_at.desc())
        .limit(200)
    ).all()
    if not rows:
        return []

    try:
        texts = [r.content for r in rows]
        emb = encode_texts([q] + texts)
        qv = emb[0]
        docv = emb[1:]
        qn = qv / (np.linalg.norm(qv) + 1e-9)
        dn = docv / (np.linalg.norm(docv, axis=1, keepdims=True) + 1e-9)
        sims = dn @ qn
        order = np.argsort(-sims)
        return [
            (texts[int(i)], float(sims[int(i)]))
            for i in order[:limit]
            if float(sims[int(i)]) >= min_similarity
        ]
    except Exception as e:
        logger.warning("Embedding ranking failed: %s", e)
        return [(r.content, 0.0) for r in rows[:limit]]


def add_memory(db: Session, user_id: UUID, content: str, source: str | None = "chat") -> UUID:
    """Persist a retrievable chunk (Postgres source of truth)."""
    text = content.strip()
    if not text:
        raise ValueError("empty content")
    row = MemoryChunk(user_id=user_id, content=text[:16000], source=source)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def search_memory(
    db: Session,
    user_id: UUID,
    query: str,
    k: int = 5,
    min_similarity: float = 0.22,
) -> list[str]:
    """
    Retrieve top-k chunks by cosine similarity to the query.
    Scans recent chunks only (cap) — swap for pgvector / FAISS / Chroma on Linux when you deploy.
    """
    if not query.strip() or k <= 0:
        return []
    ranked = rank_memory(db, user_id, query, limit=k, min_similarity=min_similarity)
    return [text for text, _score in ranked]
