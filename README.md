# FinMate

Multi-agent personal finance assistant: **FastAPI + PostgreSQL** backend, **React (Vite)** frontend, hybrid intent routing to three specialist agents, lightweight RAG memory, and optional local **Qwen2.5 + LoRA** inference.

For a full file-by-file reference, see [PROJECT_DOCUMENTATION.md](./PROJECT_DOCUMENTATION.md).

## What it does

- **Auth** — register/login with bcrypt + JWT
- **Onboarding** — income, goals, risk, location stored as retrievable memory
- **Transactions** — CRUD, monthly summaries, CSV import
- **Chat** — routes to Budget Planner, Investment Analyser, or Invoice Generator
- **Budget** — 30-day aggregates, month-over-month spending insights
- **Investment** — ticker/company detection, Yahoo Finance data, risk-based allocation
- **Invoice** — line-item parsing in chat; **PDF/image upload** → structured JSON; regenerate PDF
- **Memory (RAG)** — Postgres + sentence-transformer similarity search (not Chroma/pgvector)
- **UI** — separate login/register pages, chat layout with conversation sidebar, settings for profile and CSV import
- **Chat import/export** — **+** menu in chat for invoice PDF/image upload and CSV import; **↓** menu to export transactions or download the conversation

## Frontend pages

| Route | Purpose |
|-------|---------|
| `/login` | Sign in (redirects to `/chat` when authenticated) |
| `/register` | Create account |
| `/chat` | Main chat UI — scrollable message thread, agent selector, sidebar, **+ import** (invoice/CSV) and **↓ export** menus |
| `/settings` | Financial onboarding profile, CSV transaction import, **PDF/image invoice import** |

Conversations are stored in PostgreSQL (`chat_sessions`, `chat_messages`) and exposed via `/api/conversations`. Each chat message optionally links to a session via `session_id` on `POST /api/chat/message`.

## Architecture (runtime)

```text
React UI  →  FastAPI  →  Orchestrator
                │              ├─ hybrid router (keywords + embeddings)
                │              ├─ optional local LLM (Qwen2.5 LoRA)
                │              └─ rule-based specialists + fallbacks
                ├─ PostgreSQL (users, transactions, memory)
                └─ yfinance / ReportLab
```

Reply contract for every assistant turn: `[AGENT: BUDGET|INVESTMENT|INVOICE]` tag, natural-language prose, then a valid JSON line.

**Default agent behavior (without `FINMATE_USE_LLM`):**
- **Investment** — live Yahoo Finance quotes (last close, 20-day SMA, ranges); personalized allocation from onboarding when no ticker is confirmed. Ticker detection is case-sensitive (`MSFT` yes, lowercase “right” no).
- **Budget** — real transaction aggregates and month-over-month deltas from PostgreSQL.
- **Invoice** — parsed line items from your message.

## Prerequisites

- Docker Desktop (or Docker Engine) for PostgreSQL
- Python 3.11+
- Node.js 20+ (for the frontend)

## Quick start (all services)

From the repo root:

```bash
docker compose up -d
cd backend && python -m venv .venv
# Windows:
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
cd ..
npm install
npm run dev
```

- API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Frontend: [http://127.0.0.1:5173](http://127.0.0.1:5173) (proxies `/api` to port 8000)

Or run backend and frontend separately (see sections below).

## 1. Database

From the repo root:

```bash
docker compose up -d
```

Copy `backend/.env.example` to `backend/.env`. Defaults use **port 5433** on the host (see [Troubleshooting](#troubleshooting)).

Verify the container is up:

```bash
docker compose ps
# expect: 0.0.0.0:5433->5432/tcp
```

## 2. Backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Set **`JWT_SECRET`** in `backend/.env` to a long random string for production.

If your database already existed **before** password auth was added, run once in psql/pgAdmin:

`backend/scripts/migrate_add_password_hash.sql`, then register a new account.

**Smoke test:** `POST /api/auth/register` (email + password, min 8 chars) → use `access_token` as `Authorization: Bearer <token>` on `POST /api/chat/message`, `GET /api/users/me`, `POST /api/transactions`, `POST /api/invoices/pdf`.

**Seed demo transactions** (after you have a user id from register or `/api/users/me`):

```bash
cd backend
.venv\Scripts\python scripts/csv_seed_transactions.py --user-id <UUID> --csv ../training/data/personal_finance_tracker_dataset.csv --limit 300
```

Use `--format indian` for `Indian Personal Finance and Spending Habits.csv`. Add `--dry-run` first to preview rows.

**Evaluate routing + response format:**

```bash
cd backend
.venv\Scripts\python scripts/evaluate_chat.py --token <JWT_TOKEN> --dataset ../training/data/eval_prompts_heldout.jsonl
```

Reports routing accuracy and format compliance (tag + prose + JSON tail).

Generate a larger held-out set:

```bash
.venv\Scripts\python scripts/generate_eval_set.py --total 200 --out ../training/data/eval_prompts_heldout_200.jsonl
```

### Optional: local LLM

A fine-tuned LoRA adapter lives in `backend/app/ml/finmate-lora/` (base: **Qwen/Qwen2.5-1.5B-Instruct**). By default the backend uses **rule-based agents** only.

To enable local inference, in `backend/.env`:

```env
FINMATE_USE_LLM=true
```

Requires `torch`, `transformers`, `peft` (in `requirements.txt`) and adapter weights under `FINMATE_LORA_PATH`. The orchestrator falls back to rule-based agents if generation fails.

## 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). You will land on `/login` if not signed in; after auth, use `/chat` with the sidebar to switch or start conversations.

### Chat import & export

In the chat composer:

| Control | Action |
|---------|--------|
| **+** | Upload an **invoice PDF or image** (OCR + structured parse, then sent to the Invoice agent) or pick a **transactions CSV** file to import |
| **↓** | **Download transactions (CSV)** from your account, or **download the current conversation** as a `.txt` file |

You can also use **Settings** for onboarding, CSV paste import, and full invoice editing with PDF regeneration.

### Invoice import (PDF / image)

In **Settings → Invoice import**:

1. Upload a PDF or image (PNG/JPEG/WebP).
2. Click **Extract structured data** — returns vendor, dates, line items, subtotal/tax/total.
3. Edit fields in the UI if needed.
4. Click **Download PDF** to regenerate a clean invoice.

API (Bearer token required):

| Endpoint | Purpose |
|----------|---------|
| `POST /api/invoices/parse` | Multipart file upload → `ParseInvoiceResult` JSON |
| `POST /api/invoices/pdf` | JSON line items (+ optional header fields) → PDF bytes |
| `POST /api/invoices/pdf/structured` | Full `StructuredInvoice` body → PDF bytes |
| `GET /api/transactions/export/csv` | Download all transactions as CSV |

**Image OCR** uses [Tesseract](https://github.com/tesseract-ocr/tesseract) via `pytesseract`. Install Tesseract on your system. FinMate auto-detects common Windows install paths (`C:\Program Files\Tesseract-OCR\tesseract.exe`). If OCR still fails, set in `backend/.env`:

```env
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Restart the backend after changing `.env`. Text-based PDFs work without Tesseract (`pdfplumber`). Scanned PDFs fall back to OCR via `pymupdf` + Tesseract.

In chat, paste invoice text or line items (`1200 Web design`) — the invoice agent returns structured JSON in the reply tail.

## 4. Training (Google Colab)

1. `cd training` → `python scripts/build_finmate_train.py` → `training/data/finmate_train.jsonl`
2. `python scripts/analyze_finmate_dataset.py data/finmate_train.jsonl`
3. Subset: `python scripts/sample_finmate_small.py --input data/finmate_train.jsonl --out data/finmate_train_small.jsonl --total 4500`
4. Upload JSONL to Colab/Drive; run `training/colab/finmate_qlora_sft.ipynb`
5. Copy the adapter into `backend/app/ml/finmate-lora/` (or set `FINMATE_LORA_PATH`)

## Project layout

| Path | Purpose |
|------|--------|
| `backend/app/main.py` | FastAPI app, CORS, DB init |
| `backend/app/agents/orchestrator.py` | Hybrid routing + optional LLM |
| `backend/app/agents/intent.py` | Keyword + embedding intent classifier |
| `backend/app/agents/budget_planner.py` | Spending aggregates and insights |
| `backend/app/agents/investment_analyser.py` | Tickers, yfinance, allocation |
| `backend/app/agents/invoice_generator.py` | Line items and invoice guidance |
| `backend/app/ml/finmate.py` | Local LoRA load/generate/postprocess |
| `backend/app/rag/memory_store.py` | Embedding similarity over `MemoryChunk` |
| `backend/app/api/routes/chat.py` | Chat, context injection, reply contract, session persistence |
| `backend/app/api/routes/conversations.py` | List/create/delete conversations and messages |
| `backend/app/services/market_data.py` | Yahoo Finance client (`curl_cffi` session) |
| `backend/scripts/evaluate_chat.py` | Held-out routing/format evaluation |
| `frontend/src/pages/ChatPage.tsx` | Chat thread + sidebar + import/export menus |
| `frontend/src/components/ChatComposerMenu.tsx` | **+** import and **↓** export menus in chat |
| `frontend/src/pages/SettingsPage.tsx` | Onboarding, CSV import, PDF |
| `frontend/src/pages/LoginPage.tsx` | Login |
| `frontend/src/pages/RegisterPage.tsx` | Registration |
| `frontend/src/App.tsx` | React Router routes |
| `PROJECT_DOCUMENTATION.md` | Detailed backend/frontend reference |
| `work_division.md` | Capstone report section outline |

## Troubleshooting

### `password authentication failed for user "finmate"`

Usually one of:

1. **PostgreSQL container not running** — `docker compose up -d` from repo root; `docker compose ps` should show the db service **Up**.
2. **Wrong port** — This project maps Docker Postgres to host **5433** (not 5432) so it does not clash with a local Windows PostgreSQL service. Ensure `backend/.env` has:
   ```env
   DATABASE_URL=postgresql+psycopg2://finmate:finmate@localhost:5433/finmate
   ```
3. **Stale volume with different password** — reset the dev database (destroys data):
   ```bash
   docker compose down -v
   docker compose up -d
   ```

### Tesseract / image OCR not working

1. **Install Tesseract** — [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki) or `winget install UB-Mannheim.TesseractOCR`.
2. **Set explicit path** if the backend cannot find the binary (common when PATH is not updated for the terminal running uvicorn):
   ```env
   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```
3. **Restart the backend** after install or `.env` changes.
4. **Check the API response** — missing Tesseract returns HTTP **503** with a clear message; empty OCR returns **422** (try a clearer scan).

### Backend starts but chat is slow on first message

The sentence-transformer model (`all-MiniLM-L6-v2`) loads on the first embedding call. Subsequent requests are faster.

### Yahoo Finance errors (`Failed to get ticker`, `possibly delisted`, symbol `RIGHT`)

**False ticker `RIGHT`:** caused by uppercasing the whole message for caps detection — fixed in `backend/app/agents/ticker_utils.py` (only tokens already typed in `ALLCAPS` or `$TICKER` / company names are candidates).

Yahoo sometimes blocks plain HTTP clients. FinMate uses `curl_cffi` in `backend/app/services/market_data.py`. Reinstall backend deps if needed:

```bash
cd backend
.venv\Scripts\pip install -r requirements.txt
```

Only symbols with confirmed price history are used. If tickers still fail, check network/VPN.

### `torch_dtype` deprecation warning

Resolved in `backend/app/ml/finmate.py` (`dtype=` instead of `torch_dtype=`). Restart the backend after updating.

## Next steps (optional)

1. Enable `FINMATE_USE_LLM=true` and compare rule-based vs LoRA replies with `evaluate_chat.py`.
2. Add spending charts (e.g. Recharts) on the frontend.
3. Scale memory with **pgvector** or **FAISS** for larger histories.
