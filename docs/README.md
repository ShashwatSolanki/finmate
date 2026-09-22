# FinMate Documentation

This directory contains the maintained technical documentation for FinMate.

The repository root README is the quick-start and project overview. This directory is the detailed engineering reference.

## Documentation map

| Document | Covers |
|---|---|
| [Architecture](architecture.md) | System layers, request flow, design principles, component relationships |
| [AI System](ai-system.md) | QLoRA model, inference, training artifacts, deterministic fallbacks |
| [Agents](agents.md) | Routing, Budget, Investment, Invoice, agentic orchestration, confidence |
| [RAG & Memory](rag.md) | Embeddings, retrieval, context construction, persistence, evaluation |
| [Backend](backend.md) | FastAPI structure, database, authentication, services and APIs |
| [Invoice System](invoice-system.md) | Invoice chat flow, parsing, OCR, Invoice Studio, PDF/CSV export |
| [Frontend](frontend.md) | React/Vite pages, components, chat, settings and integration |
| [Evaluation & Testing](evaluation.md) | Unit/integration tests, RAG evaluation and final AI evaluation |
| [Deployment & Development](development.md) | Local setup, configuration, troubleshooting and development workflow |

## Recommended reading order

For a new developer:

1. Architecture
2. Backend
3. AI System
4. RAG & Memory
5. Agents
6. Invoice System
7. Frontend
8. Evaluation & Testing
9. Development

For a viva/project presentation:

1. Architecture
2. AI System
3. RAG & Memory
4. Agents
5. Evaluation & Testing

## Documentation rule

Documentation should describe the implementation that actually exists in the repository.

In particular:

- Do not describe Chroma or pgvector as the current RAG implementation.
- Do not describe recursive AutoGPT re-planning as implemented; the current agentic workflow is bounded.
- Do not claim Google OAuth, refresh tokens, or budget CRUD are implemented unless the corresponding code exists.
- Treat deterministic financial calculations and stored financial data as authoritative.
- Treat the local LLM as an optional generation/synthesis layer with deterministic fallbacks.

When implementation changes, update the relevant module document rather than expanding one monolithic file.
