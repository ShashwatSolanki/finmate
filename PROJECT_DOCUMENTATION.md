# FinMate Project Documentation

The detailed documentation has been split into focused module-level documents under `docs/`.

Start here:

- [Documentation index](docs/README.md)
- [Architecture](docs/architecture.md)
- [AI & ML System](docs/ai-system.md)
- [Agents & Orchestration](docs/agents.md)
- [RAG & Memory](docs/rag.md)
- [Backend](docs/backend.md)
- [Invoice System](docs/invoice-system.md)
- [Frontend](docs/frontend.md)
- [Evaluation & Testing](docs/evaluation.md)
- [Development, Configuration & Deployment](docs/development.md)

## Why the documentation is split

The old document combined architecture, AI, RAG, every backend file, frontend implementation, API reference, setup instructions, evaluation, and limitations into one large file. That made it difficult to navigate and easy for one section to become stale.

The new structure separates documentation by responsibility while keeping one documentation index.

The root README remains the quick-start document. This file is only a compatibility entry point for anyone who already knows the old `PROJECT_DOCUMENTATION.md` path.

## Source-of-truth rule

The documentation describes the current implementation in the repository. In particular:

- RAG currently uses PostgreSQL + MiniLM embeddings + cosine similarity; it is not a Chroma/pgvector runtime.
- Agentic orchestration is bounded; a recursive AutoGPT re-planning loop is future work.
- Deterministic financial calculations and stored financial data are authoritative.
- The local Qwen/LoRA model is optional and has deterministic fallbacks.
- Authentication documentation describes the implementation that exists in code rather than planned Google OAuth/refresh-token work.

When a module changes, update its corresponding document in `docs/` and update `docs/architecture.md` if the system flow changes.
