# FinMate Project Documentation

This document describes the **current implementation** of FinMate in the repository root. It is intended to stay synchronized with the code in `backend/`, `frontend/`, and the local model artifacts under `backend/app/ml/finmate-lora/`.

> **Scope note:** the `training/` directory contains model/data-generation assets and is summarized where it affects runtime behavior. The detailed training notebooks/scripts remain in that directory.

---

## 1. Project Overview

FinMate is a multi-agent personal finance assistant with:

- React + Vite frontend
- FastAPI backend
- PostgreSQL persistence
- JWT authentication with bcrypt password hashing
- Financial onboarding and transaction management
- Three specialist finance agents:
  - Budget Planner
  - Investment Analyser
  - Invoice Generator
- Hybrid routing using keyword signals and sentence embeddings
- Optional local Qwen2.5-1.5B LoRA inference
- Lightweight RAG memory using PostgreSQL + MiniLM embeddings + cosine similarity
- Bounded multi-agent orchestration for cross-domain requests
- Portfolio holdings and live market-value calculations
- Invoice parsing, structured invoice editing, and PDF/CSV export
- Conversation persistence
- Explainable heuristic confidence metadata
- Reproducible RAG and AI evaluation suites

The runtime deliberately keeps deterministic/database-backed financial operations authoritative, while the local LLM is used where it improves natural-language generation and synthesis.

---

## 2. System Architecture

### 2.1 Runtime architecture

```text
                         ┌─────────────────────────┐
                         │      React / Vite       │
                         │ Chat • Settings • Auth  │
                         └────────────┬────────────┘
                                      │ /api
                                      ▼
                         ┌─────────────────────────┐
                         │       FastAPI API       │
                         │ Auth • Chat • Invoices  │
                         │ Transactions • Portfolio│
                         │ Conversations           │
                         └────────────┬────────────┘
                                      │
                 ┌────────────────────┼────────────────────┐
                 │                    │                    │
                 ▼                    ▼                    ▼
        ┌────────────────┐   ┌─────────────────┐   ┌─────────────────┐
        │ Agent Layer    │   │ RAG / Memory    │   │ PostgreSQL      │
        │ Router         │   │ MiniLM + cosine │   │ Users           │
        │ Budget         │   │ recent chunks   │   │ Transactions    │
        │ Investment     │   │ onboarding      │   │ Holdings        │
        │ Invoice        │   │ chat memory     │   │ Conversations   │
        │ Agentic plan   │   └─────────────────┘   │ Memory chunks   │
        └───────┬────────┘                          └─────────────────┘
                │
       ┌────────┴─────────┐
       ▼                  ▼
┌────────────────┐  ┌──────────────────┐
│ Local Qwen +   │  │ External/tool    │
│ LoRA adapter   │  │ backed services  │
│ optional       │  │ yfinance         │
└────────────────┘  │ ReportLab/OCR    │
                    └──────────────────┘
```

### 2.2 Design principle

Deterministic code is the source of truth for operations that need reliable structured results:

- transaction aggregation
- portfolio valuation
- invoice parsing/export data
- routing safeguards
- artifact preservation

The LLM is an optional generation layer and a bounded synthesis layer. Failures fall back to deterministic specialist responses.

---

## 3. End-to-End Request Flow

A normal authenticated chat request follows this sequence:

1. Frontend sends `POST /api/chat/message` with a JWT bearer token.
2. Backend authenticates the user.
3. RAG retrieval searches recent `MemoryChunk` rows.
4. Recent conversation context and the latest onboarding profile are loaded.
5. A multi-domain request is checked for a bounded agentic plan.
6. Otherwise, routing uses the explicit specialist, follow-up safeguards, or the hybrid router.
7. The chosen specialist performs database/tool-backed work.
8. Optional local Qwen generation is used where configured.
9. The reply is normalized to the FinMate contract:
   - first line: `[AGENT: BUDGET]`, `[AGENT: INVESTMENT]`, or `[AGENT: INVOICE]`
   - natural-language response
   - final JSON object containing intent/steps/tools/notes
10. Confidence metadata is calculated.
11. High-signal user messages and useful assistant messages are stored as memory.
12. The turn is persisted in `chat_sessions` and `chat_messages`.
13. The frontend renders the cleaned response, agent badge, metadata, and invoice export actions where available.

---

## 4. Authentication and User Context

### Authentication

Relevant files:

- `backend/app/api/routes/auth.py`
- `backend/app/api/deps.py`
- `backend/app/security/passwords.py`
- `backend/app/security/jwt_tokens.py`

Registration and login use bcrypt-backed password hashing and signed JWT access tokens.

JWT payload includes:

- `sub`: user UUID
- `exp`: expiration timestamp

The frontend keeps the access token in browser `localStorage` under `finmate_token`.

### Onboarding

Users can save:

- monthly income
- location
- goals
- risk tolerance
- currency

The profile is stored as a `MemoryChunk(source="onboarding")` and later injected into chat context. This lets the budget and investment flows use the user's stored profile.

---

## 5. Data Model

The core PostgreSQL models are defined in `backend/app/db/models.py`.

### User

Stores account identity and password hash.

### Transaction

Stores:

- user
- amount
- currency
- category
- description
- occurred date

Transactions are the primary data source for budget and spending analysis.

### Budget

Stores category-level limits and periods. The table exists, but there is not currently a complete budget CRUD API.

### InvestmentHolding

Stores the user's portfolio position:

- symbol
- quantity
- average cost
- currency

It supports current-value and unrealized P/L calculations using live prices.

### MemoryChunk

Stores retrievable text such as:

- onboarding context
- high-signal user messages
- useful assistant replies

### ChatSession / ChatMessage

Provide persisted conversations with:

- session title
- user/assistant messages
- selected agent
- response metadata
- timestamps

---

## 6. RAG and Memory System

### 6.1 Implementation

The RAG layer is in:

`backend/app/rag/memory_store.py`

Embeddings use:

`sentence-transformers/all-MiniLM-L6-v2`

The embedding model is lazy-loaded in:

`backend/app/ml/embeddings.py`

with the Torch backend selected explicitly.

### 6.2 Retrieval algorithm

For a query:

1. Fetch up to the most recent 200 user memory chunks.
2. Embed the query and candidate texts.
3. Normalize the vectors.
4. Compute cosine similarity.
5. Sort descending.
6. Return the top-(k) chunks above the configured threshold.

The chat route currently requests up to 5 chunks with a minimum similarity of 0.22.

This is a lightweight implementation. It is **not** using Chroma or pgvector in the current runtime.

### 6.3 Context construction

Chat context can contain:

- recent chat turns
- latest onboarding profile
- semantically retrieved memory

The merged context is passed to the orchestrator and specialist agents.

### 6.4 Evaluation

`backend/scripts/evaluate_rag.py` provides a deterministic fixture evaluator for retrieval logic, reporting Hit@2 and MRR.

`backend/tests/test_rag_evaluation.py` covers:

- empty queries/memory
- ranking order
- similarity thresholding
- embedding-failure fallback
- recent-context ordering/capping
- latest onboarding retrieval

The fixture metrics validate retrieval logic; they are not production retrieval-quality claims.

---

## 7. Agent Routing and Orchestration

### 7.1 Specialist agents

The three specialists are:

1. `budget_planner`
2. `investment_analyser`
3. `invoice_generator`

Shared agent types live in `backend/app/agents/types.py`.

### 7.2 Hybrid router

`backend/app/agents/intent.py` combines:

- regex/keyword signals
- sentence-embedding similarity
- prototype examples

The configured embedding contribution is controlled by `intent_embedding_weight`.

### 7.3 Routing safeguards

The orchestrator in `backend/app/agents/orchestrator.py` adds safeguards so authoritative routing is preserved:

- explicit forced specialist selections are respected
- cross-domain requests can enter the bounded agentic flow
- clear specialist intent is selected before the general LLM path
- the generated LLM route cannot silently override an already-selected specialist
- structured invoice flows preserve exportable artifacts

---

## 8. Bounded Agentic Workflow

The agentic system is implemented in:

`backend/app/agents/agentic_orchestrator.py`

It is deliberately **bounded**, not an unbounded recursive AutoGPT loop.

### 8.1 Planning

`build_plan()` detects when a request spans at least two domains:

- budget
- investment
- invoice

The plan is capped by `agentic_max_steps`, which defaults to 3.

### 8.2 Execution

For each plan step:

1. Execute the selected specialist.
2. Capture its verified observation.
3. Pass previous observations as context to the next specialist.
4. Continue until the bounded plan is exhausted.

### 8.3 Synthesis

After specialist execution:

- the local model may synthesize the verified observations when available
- weak synthesis that drops specialist coverage is rejected
- deterministic synthesis preserves all specialist observations if model synthesis fails

Invoice metadata is propagated through the final response:

- `invoice_ref`
- `invoice_payload`
- `invoice_actions`
- `parsed_items_count`
- `parsed_total`
- `currency`

### 8.4 Failure handling

A failed specialist is recorded in `agents_failed`, while successful specialist observations remain available.

If every specialist fails, the agentic path returns `None` so the normal orchestrator can fall back.

### 8.5 Scope boundary

A true recursive:

`plan → act → observe → decide → re-plan → ...`

loop is **not** part of the current implementation. It remains future work.

---

## 9. Budget Agent

File:

`backend/app/agents/budget_planner.py`

The budget flow uses transaction data as its source of truth.

Typical steps:

1. Determine a 30-day lookback window.
2. Read transaction currency.
3. Aggregate spending by category.
4. Add month-over-month insights.
5. Include relevant RAG context.
6. Generate a personalized response with the local model when available.
7. Fall back to deterministic output when generation is unavailable.

Planned steps include:

- `load_transactions_30d`
- `aggregate_by_category`
- `mom_insights`
- `retrieve_rag`
- `finmate_generate`

---

## 10. Investment Agent

File:

`backend/app/agents/investment_analyser.py`

### 10.1 Ticker analysis

The agent can:

- detect explicit tickers
- map known company names to tickers
- validate symbols with Yahoo Finance
- fetch market history
- compute last close
- compute previous-session change
- compute percent change
- compute a 20-day SMA
- report session and 52-week ranges

### 10.2 Portfolio mode

For stored holdings it can report:

- quantity
- average cost
- last price
- market value
- unrealized P/L
- unrealized P/L percentage

Portfolio data comes from `InvestmentHolding`, rather than being fabricated from chat text.

### 10.3 Allocation mode

When no confirmed ticker is present, the agent can construct a deterministic allocation suggestion from:

- investment amount
- risk tolerance
- income
- location
- onboarding context

Current application heuristics:

| Profile | Equity | Debt | Cash |
|---|---:|---:|---:|
| Aggressive | 75% | 20% | 5% |
| Moderate/default | 60% | 30% | 10% |
| Conservative | 40% | 45% | 15% |

These are application-level heuristics, not guarantees of investment outcomes.

---

## 11. Invoice System

Invoice functionality spans:

- `backend/app/agents/invoice_generator.py`
- `backend/app/invoice/`
- `backend/app/api/routes/invoices.py`
- `backend/app/invoice/pdf_invoice.py`

### 11.1 Chat invoice generation

The invoice specialist can parse natural-language requests and simple item lines, including:

- description-first item lines
- amount-first item lines
- common invoice request prefixes
- expense-grounded invoice requests based on recent transactions

Expense invoice requests can turn recent available transactions into invoice-style line items.

### 11.2 Structured invoice metadata

Successful invoice responses preserve:

- invoice reference
- structured invoice payload
- export actions
- parsed item count
- parsed total
- currency

The metadata survives bounded agentic synthesis.

### 11.3 Invoice parsing

The API supports:

- text-based PDF parsing
- image OCR
- invoice CSV parsing

OCR uses Tesseract through `pytesseract`.

Invoice CSV imports can recognize:

- `invoice_no`
- `item`
- `quantity`
- `unit_price`
- `amount`
- `subtotal`
- `cgst`
- `sgst`
- `total`

CGST and SGST are combined into the invoice tax total rather than treated as line items.

### 11.4 PDF generation

ReportLab is used for PDF generation.

Supported API paths include:

- `POST /api/invoices/pdf`
- `POST /api/invoices/pdf/structured`

The structured endpoint is used when an imported or edited invoice is exported.

---

## 12. Portfolio Tracking

Portfolio support is exposed under:

- `GET /api/portfolio/summary`
- `POST /api/portfolio/holdings`
- `DELETE /api/portfolio/holdings/{symbol}`

The Settings page provides:

- holding entry
- quantity
- average cost
- currency
- live-price refresh
- invested value
- current market value
- unrealized P/L
- count of valued holdings

The investment agent consumes the same stored holdings for grounded portfolio questions.

---

## 13. Confidence Indicator

File:

`backend/app/agents/confidence.py`

Every assistant turn can include a transparent confidence indicator.

It is explicitly a **heuristic signal, not a calibrated probability**.

The score uses:

| Factor | Weight |
|---|---:|
| Execution success | 40% |
| Retrieval availability signal | 20% |
| Evidence signal | 25% |
| Response completeness | 15% |

Levels:

- high: score (ge 0.80)
- medium: score (ge 0.60)
- low: below 0.60

Metadata includes:

- `confidence`
- `confidence_level`
- `confidence_method`
- `confidence_factors`

The implementation is covered by `backend/tests/test_confidence.py`.

The frontend exposes message metadata through `MessageMetadata.tsx`. The implemented confidence mechanism is the backend score/level, not a statistically calibrated probability.

---

## 14. Conversation Persistence

Chat sessions are persisted in:

- `chat_sessions`
- `chat_messages`

Frontend capabilities include:

- create a new conversation
- select previous conversations
- delete conversations
- preserve session IDs across requests
- display the responding agent and metadata

Relevant routes are under:

`/api/conversations`

The chat endpoint accepts an optional `session_id` and attaches messages to the corresponding conversation.

---

## 15. Frontend

### Pages

- `frontend/src/pages/LoginPage.tsx`
- `frontend/src/pages/RegisterPage.tsx`
- `frontend/src/pages/ChatPage.tsx`
- `frontend/src/pages/SettingsPage.tsx`

### Important components

- `ChatSidebar.tsx`
- `ChatComposerMenu.tsx`
- `InvoiceExportActions.tsx`
- `MessageMetadata.tsx`
- `InvoiceImportPanel.tsx`

### Chat behavior

The chat page:

1. loads persisted conversations
2. sends authenticated messages
3. strips machine-readable agent tags/JSON from displayed text
4. shows agent badges
5. shows response metadata
6. renders invoice PDF/CSV export actions when structured invoice data is available
7. supports invoice/document and transaction import from the composer

### Settings behavior

The Settings page provides:

- financial profile management
- transaction CSV import
- portfolio holding management and live-price refresh
- Invoice Studio / invoice import workflows
- sample PDF generation

---

## 16. Import and Export Workflows

### Transaction CSV import

```text
CSV
  ↓
/api/transactions/import/csv
  ↓
parse aliases / delimiter
  ↓
parse Decimal amounts + dates
  ↓
create Transaction rows
  ↓
return counts + preview
  ↓
show result in chat
```

### Invoice import

```text
PDF / Image / Invoice CSV
  ↓
invoice extraction
  ↓
StructuredInvoice
  ↓
chat preview / Invoice Studio
  ↓
edit if needed
  ↓
PDF or CSV export
```

### Chat exports

The chat composer also exposes transaction CSV export and conversation text download.

---

## 17. Reply Contract

FinMate normalizes assistant responses into:

```text
[AGENT: BUDGET|INVESTMENT|INVOICE]

Natural-language response...

{"intent":"...","steps":[...],"tools_needed":[...],"notes":"..."}
```

The API enforces:

1. valid agent tag
2. prose response
3. final JSON object

This contract supports frontend rendering, evaluation, routing inspection, and deterministic fallback behavior.

The frontend removes the tag and JSON tail before showing the natural-language response.

---

## 18. Local Model and LoRA Adapter

Runtime model files live in:

`backend/app/ml/finmate-lora/`

Configured model:

- base: `Qwen/Qwen2.5-1.5B-Instruct`
- PEFT: LoRA
- rank: 16
- alpha: 32
- dropout: 0.05
- target modules: `q_proj`, `v_proj`
- task: causal language modeling

Runtime loading is handled by:

`backend/app/ml/finmate.py`

The loader:

1. resolves the adapter directory
2. reads `adapter_config.json`
3. loads the base model/tokenizer
4. loads PEFT adapter weights
5. applies the tokenizer chat template when available
6. generates deterministically with sampling disabled
7. postprocesses the output into the reply contract

If adapter files or runtime dependencies are unavailable, the application falls back to deterministic specialist behavior.

---

## 19. Evaluation and Testing

### 19.1 Unit and integration coverage

Dedicated tests cover:

- RAG retrieval and context construction
- confidence calculation
- data-backed investment behavior
- data-backed invoice behavior
- bounded agentic execution
- invoice artifact preservation
- routing edge cases

The final Member 2 development cycle reached **29 backend unit tests passing** locally.

### 19.2 RAG evaluator

`backend/scripts/evaluate_rag.py`

Reports fixture-based:

- Hit@2
- MRR

### 19.3 Final AI evaluator

`backend/scripts/evaluate_ai.py`

The evaluator sends held-out prompts to a running backend and checks:

- HTTP success
- routing accuracy
- reply-format compliance
- confidence metadata coverage
- observed RAG usage
- bounded agentic-plan execution
- invoice artifact preservation

Dataset:

`training/data/final_ai_eval.jsonl`

Examples:

```bash
python scripts/evaluate_ai.py --token <JWT_TOKEN>
python scripts/evaluate_ai.py --token <JWT_TOKEN> --quick
python scripts/evaluate_ai.py --token <JWT_TOKEN> --cases 3,5,11
```

The dataset is an implementation regression suite, not a general model-quality or production-performance benchmark.

---

## 20. API Reference

### Health

```text
GET /api/health
```

### Authentication

```text
POST /api/auth/register
POST /api/auth/login
```

### Users

```text
GET  /api/users/me
POST /api/users/onboarding
GET  /api/users/onboarding/latest
GET  /api/users/onboarding/profile
```

### Transactions

```text
POST /api/transactions
GET  /api/transactions
GET  /api/transactions/summary/monthly
POST /api/transactions/import/csv
GET  /api/transactions/export/csv
```

### Portfolio

```text
GET    /api/portfolio/summary
POST   /api/portfolio/holdings
DELETE /api/portfolio/holdings/{symbol}
```

### Invoices

```text
POST /api/invoices/parse
POST /api/invoices/parse/csv
POST /api/invoices/pdf
POST /api/invoices/pdf/structured
```

### Agents

```text
GET /api/agents
```

### Chat

```text
POST /api/chat/message
```

### Conversations

```text
GET    /api/conversations
POST   /api/conversations
PATCH  /api/conversations/{session_id}
DELETE /api/conversations/{session_id}
GET    /api/conversations/{session_id}/messages
```

---

## 21. Repository Structure

```text
finmate/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   ├── agentic_orchestrator.py
│   │   │   ├── budget_planner.py
│   │   │   ├── confidence.py
│   │   │   ├── intent.py
│   │   │   ├── invoice_generator.py
│   │   │   ├── investment_analyser.py
│   │   │   └── orchestrator.py
│   │   ├── api/
│   │   │   └── routes/
│   │   ├── db/
│   │   ├── invoice/
│   │   ├── ml/
│   │   │   ├── embeddings.py
│   │   │   ├── finmate.py
│   │   │   └── finmate-lora/
│   │   ├── rag/
│   │   ├── security/
│   │   └── services/
│   ├── scripts/
│   │   ├── evaluate_ai.py
│   │   ├── evaluate_rag.py
│   │   └── csv_seed_transactions.py
│   └── tests/
├── frontend/
│   └── src/
├── training/
│   ├── data/
│   ├── scripts/
│   └── colab/
├── docker-compose.yml
├── package.json
├── README.md
└── PROJECT_DOCUMENTATION.md
```

---

## 22. Configuration

Important settings in `backend/app/config.py` include:

| Setting | Current role |
|---|---|
| `database_url` | PostgreSQL connection |
| `jwt_secret` | JWT signing secret |
| `jwt_algorithm` | JWT algorithm |
| `access_token_expire_minutes` | JWT lifetime |
| `embedding_model_name` | MiniLM embedding model |
| `intent_embedding_weight` | hybrid routing weight |
| `finmate_lora_path` | local LoRA adapter |
| `finmate_use_llm` | enable local generation |
| `finmate_max_new_tokens` | generation cap |
| `finmate_agentic_mode` | enable bounded agentic workflow |
| `agentic_max_steps` | maximum specialist steps |
| `tesseract_cmd` | optional OCR executable path |

For real deployments, `JWT_SECRET` should be replaced with a strong environment-provided secret.

---

## 23. Development and Startup

Typical local startup:

```bash
docker compose up -d

cd backend
python -m venv .venv
# Windows
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

cd ../frontend
npm install
npm run dev
```

The project also provides root-level convenience scripts.

Frontend:

`http://127.0.0.1:5173`

Backend:

`http://127.0.0.1:8000`

Swagger:

`http://127.0.0.1:8000/docs`

---

## 24. Current Limitations

1. Memory retrieval scans a capped recent set of Postgres rows and computes similarity locally.
2. There is no persistent pgvector/FAISS/Chroma retrieval layer in the current runtime.
3. The Budget table exists without a complete CRUD API.
4. Local model inference can be slow on CPU.
5. Yahoo Finance availability is external and can fail.
6. Tesseract is required for scanned-image OCR.
7. Confidence is an explainable heuristic, not a calibrated probability.
8. The agentic workflow is bounded to three specialists by default and is not a recursive AutoGPT loop.
9. The evaluation suite is an implementation regression suite, not a production benchmark.
10. Optional model/training artifacts can be large relative to the application source.

---

## 25. Future Work

Potential extensions include:

- recursive agent re-planning for longer tasks
- scalable vector-backed memory
- richer budget CRUD and budget history
- spending visualizations
- additional market-data providers
- calibrated confidence estimation
- broader evaluation datasets and human evaluation
- production-grade database migrations
- expanded document extraction workflows

---

## 26. Current Project State

The current `main` branch contains the integrated implementation for the major AI/agent scope:

- local LoRA adapter integration
- model inference and deterministic fallbacks
- RAG memory/context retrieval
- budget, investment, and invoice specialists
- bounded multi-agent orchestration
- portfolio grounding
- invoice artifacts and exports
- explainable confidence metadata
- RAG evaluation
- final AI regression evaluation tooling
- unit/integration testing
- persisted conversations and current frontend integration

This document and the code in `main` should be treated as the source of truth for the current project behavior.

