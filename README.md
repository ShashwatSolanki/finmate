# FinMate (capstone scaffold)

Personal finance assistant: **FastAPI + PostgreSQL** backend, **React (Vite)** frontend, **Colab QLoRA** training templates. The chat orchestrator is a **stub** until you plug in your fine-tuned model in `backend/app/agents/orchestrator.py`.

## Prerequisites

- Docker Desktop (or Docker Engine) for PostgreSQL
- Python 3.11+
- Node.js 20+ (for the frontend)

## 1. Database

From the repo root:

```bash
docker compose up -d
```

Copy `backend/.env.example` to `backend/.env` (defaults match `docker-compose.yml`).

## 2. Backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

If your database already existed **before** password auth, add the column once:

`psql` / pgAdmin: run `backend/scripts/migrate_add_password_hash.sql`, then **register a new account** in the UI (or via `POST /api/auth/register`).

Copy `backend/.env.example` to `backend/.env` and set **`JWT_SECRET`** to a long random string for production.

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

**Smoke test:** `POST /api/auth/register` with email + password (min 8 chars) → use returned **`access_token`** as `Authorization: Bearer <token>` on `POST /api/chat/message`, `GET /api/users/me`, `POST /api/transactions`, `POST /api/invoices/pdf`.

**Seed demo transactions from CSV** (after you have a user id from register or `/api/users/me`):

```bash
cd backend
.venv\\Scripts\\python scripts/csv_seed_transactions.py --user-id <UUID> --csv ../training/data/personal_finance_tracker_dataset.csv --limit 300
```

Use `--format indian` for `Indian Personal Finance and Spending Habits.csv`. Add `--dry-run` first to preview rows without writing to the database.

**Evaluate routing + response format (held-out prompts):**

```bash
cd backend
.venv\\Scripts\\python scripts/evaluate_chat.py --token <JWT_TOKEN> --dataset ../training/data/eval_prompts_heldout.jsonl
```

This reports:
- routing accuracy (`expected_agent` vs returned `agent`)
- format compliance (tag + natural language + valid JSON final line)

Generate a larger 200-prompt held-out set:

```bash
cd backend
.venv\\Scripts\\python scripts/generate_eval_set.py --total 200 --out ../training/data/eval_prompts_heldout_200.jsonl
```

## 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The dev server proxies `/api` to the backend on port 8000.

## 4. Training (Google Colab)

1. Build the merged dataset: `cd training` then `python scripts/build_finmate_train.py`. This writes **`training/data/finmate_train.jsonl`** with **`[AGENT: BUDGET|INVESTMENT|INVOICE]`** tags, natural-language reasoning, and a valid JSON line per assistant reply.
2. Check distribution: `python scripts/analyze_finmate_dataset.py data/finmate_train.jsonl`
3. Create a **first-run** subset (~4.5k, ~45/35/20 mix): `python scripts/sample_finmate_small.py --input data/finmate_train.jsonl --out data/finmate_train_small.jsonl --total 4500`
5. Upload **`finmate_train_small.jsonl`** (or the full file) to Colab / Drive.
6. Open `training/colab/finmate_qlora_sft.ipynb`, set `DATA_PATH` to that file, choose `BASE_MODEL` (e.g. Mistral-7B-Instruct), run QLoRA.
7. Save the adapter to Drive or a private Hugging Face repo.

Point your report to: base model name, LoRA rank, epochs, learning rate, dataset size, and evaluation notes.

## Project layout

| Path | Purpose |
|------|--------|
| `backend/app/main.py` | FastAPI app, CORS, DB init |
| `backend/app/db/models.py` | Users (password hash), transactions, budgets, `MemoryChunk` |
| `backend/app/rag/memory_store.py` | RAG: similarity-ranked retrieval with configurable threshold |
| `backend/app/api/routes/auth.py` | Register / login → JWT |
| `backend/app/api/routes/chat.py` | Chat endpoint with recent-turn + onboarding context injection + selective memory writes |
| `backend/app/api/routes/users.py` | `/users/onboarding` profile capture stored for context |
| `backend/app/api/routes/transactions.py` | CRUD + `/transactions/import/csv` bulk import from pasted CSV |
| `backend/app/agents/orchestrator.py` | Routes to 3 agents; inject `rag_context` for your LLM |
| `backend/scripts/evaluate_chat.py` | Held-out evaluation runner for routing and output format |
| `backend/scripts/generate_eval_set.py` | Synthetic balanced held-out prompt generator |
| `training/colab/finmate_qlora_sft.ipynb` | QLoRA SFT template (Mistral/Llama-class) |
| `frontend/src/App.tsx` | Register / login, chat, sample PDF download |

## Next steps (recommended order)

1. Seed transactions; exercise chat + `/api/invoices/pdf` with a Bearer token.
2. Expand `example_sft.jsonl` and fine-tune **Mistral-7B-Instruct** or **Llama-3.1-8B-Instruct** in Colab; load the adapter in `orchestrator` / per-agent modules.
3. Optional: **pgvector** or **FAISS** on Linux for large-scale memory; optional **Celery** for monthly jobs.
4. Optional: charts (Recharts) on the frontend for spending trends.
