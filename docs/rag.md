# RAG & Memory

## 1. Purpose

FinMate uses lightweight retrieval-augmented memory so that useful user context can survive across conversations.

The current implementation uses PostgreSQL for storage and Sentence Transformers for embeddings.

It does **not** use Chroma or pgvector.

## 2. Components

| Component | Implementation |
|---|---|
| Memory storage | PostgreSQL `MemoryChunk` |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| Vector math | normalized NumPy embeddings |
| Similarity | cosine similarity |
| Retrieval scope | latest 200 user memory chunks |
| Chat retrieval | up to 5 relevant chunks |
| Minimum similarity | 0.22 in the chat retrieval path |

## 3. Memory sources

Memory can contain:

- onboarding context
- high-signal user messages
- useful assistant replies

Not every message is stored. The chat route applies a signal filter to avoid filling memory with low-value content.

## 4. Retrieval flow

```text
User message
     │
     ▼
Build query embedding
     │
     ▼
Load recent MemoryChunk rows
     │
     ▼
Embed candidate memories
     │
     ▼
Normalize vectors
     │
     ▼
Cosine similarity
     │
     ▼
Sort by similarity
     │
     ▼
Apply threshold
     │
     ▼
Top-k context
     │
     ▼
Chat / Orchestrator / Specialist
```

The current implementation is intentionally simple and suitable for a capstone-scale memory store.

## 5. Context construction

The chat context can combine three sources:

1. recent conversation turns
2. latest onboarding profile
3. semantically retrieved memory

This prevents the system from depending exclusively on semantic similarity for short conversational follow-ups.

## 6. Embedding implementation

File:

```text
backend/app/ml/embeddings.py
```

The Sentence Transformer is lazy-loaded and cached.

The Torch backend is selected explicitly to avoid unnecessary TensorFlow/Keras probing in the local runtime.

Embeddings are normalized before similarity calculations.

## 7. Memory implementation

File:

```text
backend/app/rag/memory_store.py
```

Important operations:

- add memory
- rank memories
- search memory

The current retrieval scans a capped recent set rather than maintaining a dedicated vector index.

## 8. Evaluation

RAG evaluation is implemented in:

```text
backend/scripts/evaluate_rag.py
backend/tests/test_rag_evaluation.py
```

The evaluator reports:

- Hit@2
- MRR

The fixture is deterministic and validates retrieval behavior. These numbers should not be presented as a production-scale RAG benchmark.

## 9. Scaling path

If the memory store grows substantially, the current in-process scan can be replaced or supplemented with:

- pgvector
- FAISS
- another persistent vector index

That is future architecture, not the current implementation.
