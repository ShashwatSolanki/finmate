# FinMate

Multi-agent personal finance assistant: **FastAPI + PostgreSQL** backend, **React (Vite)** frontend, hybrid intent routing to three specialist agents, lightweight RAG memory, and optional local **Qwen2.5 + LoRA** inference.

For detailed technical documentation, see [docs/README.md](./docs/README.md). The legacy [PROJECT_DOCUMENTATION.md](./PROJECT_DOCUMENTATION.md) is now a compatibility index.

## What it does

- **Auth** — register/login with bcrypt + JWT
- **Onboarding** — income, goals, risk, location stored as retrievable memory
- **Transactions** — CRUD, monthly summaries, flexible bank/export CSV import with an in-chat preview
- **Chat** — routes to Budget Planner, Investment Analyser, or Invoice Generator
- **Budget** — 30-day aggregates, month-over-month spending insights
- **Investment** — ticker/company detection, Yahoo Finance data, portfolio grounding, risk-based allocation
- **Invoice** — chat-created drafts with PDF/CSV exports; PDF/image/invoice-CSV parsing; editable Invoice Studio
- **Memory (RAG)** — Postgres + sentence-transformer similarity search (not Chroma/pgvector)
- **UI** — login/register, chat, conversation sidebar, settings, invoice workflows
- **Chat import/export** — invoice/document and CSV import plus transaction/conversation export

## Architecture

```text
React UI → FastAPI → Orchestrator
                     ├─ hybrid router
                     ├─ bounded agentic planner for multi-domain requests
                     ├─ optional local Qwen2.5 LoRA
                     └─ deterministic specialists + fallbacks
             ├─ PostgreSQL
             └─ yfinance / invoice services / OCR
```

The assistant response uses a fixed machine-readable contract: an agent tag, natural-language prose, and a final JSON object.

See [docs/architecture.md](./docs/architecture.md) for the full system design.

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

## Local LLM

The local adapter is based on **Qwen/Qwen2.5-1.5B-Instruct** with LoRA.

The model is optional. Enable it with:

```env
FINMATE_USE_LLM=true
```

If model loading or generation fails, the deterministic specialist paths remain available.

## Documentation

| Document | Purpose |
|---|---|
| [Architecture](docs/architecture.md) | System architecture and end-to-end flow |
| [AI & ML](docs/ai-system.md) | QLoRA model and inference |
| [Agents](docs/agents.md) | Specialists, routing and agentic workflow |
| [RAG & Memory](docs/rag.md) | Retrieval and persistent context |
| [Backend](docs/backend.md) | APIs, database and backend structure |
| [Invoice System](docs/invoice-system.md) | Parsing, OCR, Invoice Studio and exports |
| [Frontend](docs/frontend.md) | Pages, components and API integration |
| [Evaluation & Testing](docs/evaluation.md) | Tests, RAG evaluation and AI regression |
| [Development](docs/development.md) | Setup, configuration and troubleshooting |

## Training

Training assets live under `training/`. The detailed training notebook/scripts remain there rather than being duplicated in runtime documentation.

## Important implementation notes

- RAG uses PostgreSQL + MiniLM + cosine similarity.
- The agentic workflow is bounded to a maximum of 3 specialist steps by default.
- A recursive AutoGPT-style re-planning loop is future work.
- Portfolio values come from stored holdings and market-data services.
- Invoice artifacts are structured and exportable rather than being represented only as generated prose.
- Confidence is an explainable heuristic, not a calibrated probability.
