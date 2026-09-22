# FinMate

Multi-agent personal finance assistant: **FastAPI + PostgreSQL** backend, **React (Vite)** frontend, hybrid intent routing to three specialist agents, lightweight RAG memory, and optional local **Qwen2.5 + LoRA** inference.

## What it does

- **Auth** — register/login with bcrypt + JWT
- **Onboarding** — income, goals, risk, location stored as retrievable memory
- **Transactions** — CRUD, monthly summaries, flexible bank/export CSV import with an in-chat preview
- **Chat** — routes to Budget Planner, Investment Analyser, or Invoice Generator
- **Budget** — 30-day aggregates, month-over-month spending insights
- **Investment** — ticker/company detection, Yahoo Finance data, portfolio grounding, risk-based allocation
- **Invoice** — chat-created drafts with PDF/CSV exports; PDF/image/invoice-CSV parsing; editable Invoice Studio
- **Memory (RAG)** — PostgreSQL + sentence-transformer similarity search (not Chroma/pgvector)
- **UI** — login/register, chat, conversation sidebar, settings, invoice workflows
- **Chat import/export** — invoice/document and CSV import plus transaction/conversation export

## Architecture

```text
React UI
   │
   ▼
FastAPI API
   │
   ├── Authentication / user context
   ├── Chat / conversations
   ├── Transactions
   ├── Portfolio
   └── Invoices
   │
   ▼
Context + Orchestration
   │
   ├── RAG retrieval
   ├── recent conversation context
   ├── onboarding context
   ├── hybrid specialist routing
   └── bounded agentic planning
   │
   ├──────────────┬───────────────┬───────────────┐
   ▼              ▼               ▼
 Budget        Investment       Invoice
 Agent         Agent            Agent
   │              │               │
   ▼              ▼               ▼
PostgreSQL     Yahoo Finance    Invoice services
transactions   + holdings      + OCR + PDF/CSV
   │
   ├── MiniLM + cosine memory
   └── optional Qwen2.5-1.5B LoRA
```

### Core design principle

Deterministic code and stored financial data are authoritative for operations such as transaction aggregation, portfolio valuation, invoice totals, routing safeguards and exports. The local LLM is an optional generation/synthesis layer with deterministic fallbacks.

The current agentic workflow is **bounded**, not a recursive AutoGPT loop.

## Prerequisites

- Docker Desktop (or Docker Engine) for PostgreSQL
- Python 3.11+
- Node.js 20+

## Quick start

From the repo root:

```bash
docker compose up -d

cd backend
python -m venv .venv
# Windows:
.venv\\Scripts\\pip install -r requirements.txt
copy .env.example .env

cd ..
npm install
npm run dev
```

- API docs: http://127.0.0.1:8000/docs
- Frontend: http://127.0.0.1:5173

For separate backend/frontend startup and troubleshooting, see [Development](docs/development.md).

## Local LLM

The local adapter is based on **Qwen/Qwen2.5-1.5B-Instruct** with LoRA.

Current adapter configuration:

| Parameter | Value |
|---|---:|
| LoRA rank | 16 |
| LoRA alpha | 32 |
| LoRA dropout | 0.05 |
| Target modules | `q_proj`, `v_proj` |

The model is optional. Enable it with:

```env
FINMATE_USE_LLM=true
```

If model loading or generation fails, deterministic specialist paths remain available.

## Documentation

The README is the single entry point. Detailed technical material is organized by subsystem rather than stored in one large documentation file.

| Document | Purpose |
|---|---|
| [Architecture](docs/architecture.md) | System architecture and end-to-end request flow |
| [AI & ML](docs/ai-system.md) | QLoRA model, inference and training relationship |
| [Agents](docs/agents.md) | Budget, Investment, Invoice, routing, orchestration and confidence |
| [RAG & Memory](docs/rag.md) | Embeddings, retrieval, context construction and memory |
| [Backend](docs/backend.md) | FastAPI, PostgreSQL, authentication, services and APIs |
| [Invoice System](docs/invoice-system.md) | Invoice parsing, OCR, Invoice Studio and PDF/CSV exports |
| [Frontend](docs/frontend.md) | React/Vite pages, components and API integration |
| [Evaluation & Testing](docs/evaluation.md) | Unit/integration tests, RAG evaluation and AI regression |
| [Development](docs/development.md) | Local setup, configuration and troubleshooting |

### Recommended reading order

**New developer:** Architecture → Backend → AI & ML → RAG → Agents → Invoice → Frontend → Evaluation → Development

**Viva/presentation:** Architecture → AI & ML → RAG → Agents → Evaluation

## Training

Training assets live under `training/`. The detailed notebooks and scripts remain with those assets rather than being duplicated in runtime documentation.

## Important implementation notes

- RAG currently uses PostgreSQL `MemoryChunk` rows, `all-MiniLM-L6-v2` embeddings and cosine similarity.
- The chat retrieval path uses a capped recent memory set and top-k semantic retrieval.
- The agentic workflow is bounded to a maximum of 3 specialist steps by default.
- A recursive `plan → act → observe → re-plan` loop is future work.
- Portfolio analysis is grounded in stored holdings and market-data services.
- Invoice responses preserve structured artifacts for PDF/CSV export.
- Confidence is an explainable heuristic, not a calibrated probability.
- The current authentication implementation is password + JWT based; documentation should not claim Google OAuth or refresh-token rotation unless corresponding code is added.
- The Budget model exists, but a complete budget CRUD API is not currently exposed.

## Source-of-truth rule

Documentation describes the implementation that actually exists in the repository.

When a module changes:

1. update the corresponding document in `docs/`
2. update [Architecture](docs/architecture.md) if the system flow changes
3. update [Evaluation & Testing](docs/evaluation.md) if tests or evaluation behavior changes
4. update this README when setup or user-visible functionality changes

Avoid duplicating detailed implementation material across multiple files.

## Project structure

```text
finmate/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   ├── api/
│   │   ├── db/
│   │   ├── invoice/
│   │   ├── ml/
│   │   ├── rag/
│   │   ├── security/
│   │   └── services/
│   ├── scripts/
│   └── tests/
├── frontend/
│   └── src/
├── training/
│   ├── data/
│   ├── scripts/
│   └── colab/
├── docs/
│   ├── architecture.md
│   ├── ai-system.md
│   ├── agents.md
│   ├── rag.md
│   ├── backend.md
│   ├── invoice-system.md
│   ├── frontend.md
│   ├── evaluation.md
│   └── development.md
├── docker-compose.yml
├── package.json
└── README.md
```
