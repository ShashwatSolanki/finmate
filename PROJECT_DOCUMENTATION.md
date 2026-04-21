# FinMate Project Documentation

This document explains the FinMate project excluding the `training/` folder.

FinMate is a personal finance assistant built with a FastAPI backend, PostgreSQL database, React/Vite frontend, rule-based specialist agents, optional local LoRA-based LLM inference, JWT authentication, PDF invoice generation, transaction import, and lightweight RAG-style memory retrieval.

## 1. High-Level Overview

FinMate helps a user:

- Register and log in securely.
- Save a financial onboarding profile.
- Import or create transactions.
- Chat with a financial assistant.
- Route chat messages to the correct specialist agent.
- Generate budget advice from transaction data.
- Analyse investments using ticker detection and Yahoo Finance data.
- Prepare invoice-related responses and generate sample invoice PDFs.
- Store useful conversation and onboarding context as retrievable memory.

The application has three main runtime layers:

1. **Frontend:** React single-page app in `frontend/`.
2. **Backend:** FastAPI application in `backend/`.
3. **Database:** PostgreSQL service from `docker-compose.yml`.

There is also an optional local LLM adapter under `backend/app/ml/finmate-lora/`.

## 2. Main Runtime Pipeline

The normal application flow is:

1. User starts PostgreSQL through Docker Compose.
2. User starts the FastAPI backend.
3. User starts the React/Vite frontend.
4. User registers or logs in.
5. Backend creates a JWT access token.
6. Frontend stores the token in browser `localStorage`.
7. User optionally saves onboarding information.
8. Backend stores onboarding as a `MemoryChunk`.
9. User sends a chat message.
10. Backend authenticates the JWT.
11. Backend retrieves:
    - recent conversation memory,
    - latest onboarding profile,
    - semantically similar memory chunks.
12. Backend chooses an agent:
    - forced agent from request, or
    - follow-up override, or
    - hybrid auto-router, or
    - optional LLM route when enabled.
13. Chosen agent generates a reply.
14. Backend enforces the reply contract:
    - first line: `[AGENT: BUDGET]`, `[AGENT: INVESTMENT]`, or `[AGENT: INVOICE]`,
    - middle: plain English response,
    - final line: valid JSON.
15. Backend stores high-signal user messages and useful assistant replies as memory.
16. Frontend displays the cleaned assistant reply, planned steps, and metadata.

## 3. Agent Pipeline

The agent system lives in `backend/app/agents/`.

There are three agents:

- `budget_planner`
- `invoice_generator`
- `investment_analyser`

The central function is:

```python
run_turn(user_id, user_message, db, agent=None, rag_context=None)
```

This function is defined in `backend/app/agents/orchestrator.py`.

### 3.1 Agent Routing

Routing happens in this order:

1. If the frontend sends a forced agent, that agent is used.
2. If the message looks like a follow-up to a previous investment conversation, the chat route can force investment again.
3. If local LLM mode is enabled and no forced agent is provided, the system tries one LLM call and reads the `[AGENT: ...]` tag from the generated reply.
4. If local LLM mode is disabled or fails, the system uses hybrid intent classification.

Hybrid intent classification uses:

- regex keyword matching,
- sentence embedding similarity,
- prototype examples for each agent.

The embedding model is:

```text
sentence-transformers/all-MiniLM-L6-v2
```

### 3.2 Agent Result Shape

Each agent returns an `AgentResult`:

```python
AgentResult(
    agent=AgentName.BUDGET_PLANNER,
    reply="...",
    planned_steps=[...],
    metadata={...},
)
```

The chat route then converts this into the API response:

```json
{
  "agent": "budget_planner",
  "reply": "...",
  "planned_steps": ["..."],
  "metadata": {}
}
```

## 4. LLM Used

The backend includes an optional local PEFT/LoRA model adapter.

Adapter folder:

```text
backend/app/ml/finmate-lora/
```

The adapter metadata shows:

```text
Base model: Qwen/Qwen2.5-1.5B-Instruct
PEFT type: LoRA
Task type: CAUSAL_LM
LoRA rank: 16
LoRA alpha: 32
LoRA dropout: 0.05
Target modules: q_proj, v_proj
```

Runtime loading happens in:

```text
backend/app/ml/finmate.py
```

Important config values in `backend/app/config.py`:

```python
finmate_lora_path = "app/ml/finmate-lora"
finmate_use_llm = False
finmate_max_new_tokens = 256
```

Because `finmate_use_llm` defaults to `False`, the backend normally uses rule-based specialists unless `.env` enables local LLM inference.

If LLM mode is enabled:

1. The backend finds the LoRA adapter folder.
2. It reads the base model name from `adapter_config.json`.
3. It loads the base model using Hugging Face Transformers.
4. It loads adapter weights using PEFT.
5. It formats the prompt using the tokenizer chat template if available.
6. It generates a deterministic response with `do_sample=False`.
7. It postprocesses the response into the FinMate reply contract.

## 5. Backend Structure

```text
backend/
  app/
    api/
      routes/
    agents/
    db/
    invoice/
    ml/
    rag/
    security/
    services/
    main.py
    config.py
  scripts/
  requirements.txt
```

## 6. Backend Files Explained

### `backend/requirements.txt`

Lists Python dependencies for the backend.

Important packages:

- `fastapi`: API framework.
- `uvicorn`: ASGI server.
- `sqlalchemy`: ORM.
- `psycopg2-binary`: PostgreSQL driver.
- `pydantic`, `pydantic-settings`: validation and settings.
- `python-jose`: JWT handling.
- `passlib`, `bcrypt`: password hashing.
- `sentence-transformers`: embeddings.
- `numpy`: vector math.
- `yfinance`: market data.
- `reportlab`: PDF invoice generation.
- `torch`, `transformers`, `peft`, `accelerate`, `safetensors`: local LLM loading.

### `backend/app/__init__.py`

Package marker for the backend app. It allows imports like:

```python
from app.config import settings
```

### `backend/app/main.py`

Creates the FastAPI application.

Important behavior:

- Imports ORM models so SQLAlchemy metadata is registered.
- Calls `init_db()` during app startup.
- Enables CORS for:
  - `http://localhost:5173`
  - `http://127.0.0.1:5173`
- Includes all API routes under `/api`.
- Defines root route `/` returning service info.

Important function:

```python
lifespan()
```

This startup lifecycle function initializes DB tables before the API starts serving.

### `backend/app/config.py`

Defines application settings using `pydantic-settings`.

Important fields:

- `app_name`: API display name.
- `database_url`: PostgreSQL connection URL.
- `jwt_secret`: JWT signing secret.
- `jwt_algorithm`: JWT algorithm, default `HS256`.
- `access_token_expire_minutes`: token expiry duration.
- `embedding_model_name`: sentence-transformer model.
- `intent_embedding_weight`: weight used in hybrid router.
- `alpha_vantage_api_key`: optional key, currently not heavily used.
- `finmate_lora_path`: local LoRA adapter path.
- `finmate_use_llm`: enables/disables local LLM use.
- `finmate_max_new_tokens`: generation length cap.

### `backend/app/db/base.py`

Defines the SQLAlchemy declarative base:

```python
class Base(DeclarativeBase):
    pass
```

All ORM models inherit from this base.

### `backend/app/db/session.py`

Handles database connection and session lifecycle.

Important objects/functions:

- `engine`: SQLAlchemy engine using `settings.database_url`.
- `SessionLocal`: session factory.
- `get_db()`: FastAPI dependency that yields a DB session and closes it after request.
- `init_db()`: creates all tables from ORM metadata.

### `backend/app/db/models.py`

Defines database tables.

#### `User`

Stores app users.

Fields:

- `id`: UUID primary key.
- `email`: unique email.
- `display_name`: optional name.
- `password_hash`: hashed password.
- `created_at`: timestamp.

Relationships:

- `transactions`
- `memory_chunks`

#### `Transaction`

Stores user transactions.

Fields:

- `id`: UUID primary key.
- `user_id`: foreign key to user.
- `amount`: decimal value.
- `currency`: currency code.
- `category`: transaction category.
- `description`: optional text.
- `occurred_on`: transaction date.
- `created_at`: timestamp.

#### `Budget`

Stores budget limits.

Fields:

- `id`
- `user_id`
- `category`
- `limit_amount`
- `period_start`
- `period_end`

Currently this table exists but there are no full budget CRUD routes.

#### `MemoryChunk`

Stores RAG memory.

Fields:

- `id`
- `user_id`
- `content`
- `source`
- `created_at`

Sources include:

- `chat`
- `onboarding`

### `backend/app/api/__init__.py`

Package marker for API modules.

### `backend/app/api/routes/__init__.py`

Builds the central API router.

Included routes:

- `/api/health`
- `/api/auth`
- `/api/users`
- `/api/transactions`
- `/api/invoices`
- `/api/agents`
- `/api/chat`

### `backend/app/api/routes/health.py`

Defines:

```python
GET /api/health
```

Returns:

```json
{"status": "ok"}
```

Used as a simple health check.

### `backend/app/api/routes/auth.py`

Handles registration and login.

Pydantic models:

- `RegisterBody`
- `LoginBody`
- `TokenOut`

Important functions:

#### `register()`

Endpoint:

```text
POST /api/auth/register
```

Workflow:

1. Validate email/password.
2. Check if email already exists.
3. Hash password.
4. Create user.
5. Commit to DB.
6. Create JWT.
7. Return token and user ID.

#### `login()`

Endpoint:

```text
POST /api/auth/login
```

Workflow:

1. Find user by email.
2. Verify password hash.
3. Create JWT.
4. Return token and user ID.

### `backend/app/api/deps.py`

Defines authentication dependencies.

Important functions:

#### `get_current_user()`

Workflow:

1. Reads bearer token from `Authorization` header.
2. Decodes JWT subject.
3. Converts subject to UUID.
4. Fetches user from DB.
5. Rejects invalid/missing users.
6. Returns the `User` object.

#### `get_current_user_id()`

Returns only the current user UUID.

### `backend/app/security/passwords.py`

Password hashing utilities.

Functions:

#### `hash_password(plain)`

Hashes a plaintext password using bcrypt through Passlib.

#### `verify_password(plain, hashed)`

Checks a plaintext password against stored hash.

### `backend/app/security/jwt_tokens.py`

JWT token helpers.

Functions:

#### `create_access_token(user_id)`

Creates a JWT containing:

- `sub`: user UUID
- `exp`: expiry timestamp

#### `decode_token_subject(token)`

Decodes token, validates signature/expiry, returns user UUID or `None`.

### `backend/app/api/routes/users.py`

User and onboarding routes.

Models:

- `UserOut`
- `OnboardingBody`
- `OnboardingOut`

Endpoints:

#### `GET /api/users/me`

Returns current authenticated user.

#### `POST /api/users/onboarding`

Stores financial profile as memory.

Input:

- monthly income
- location
- goals
- risk tolerance
- currency

It creates a formatted profile string and stores it as:

```python
MemoryChunk(source="onboarding")
```

#### `GET /api/users/onboarding/latest`

Fetches the newest onboarding memory for the current user.

### `backend/app/api/routes/transactions.py`

Transaction APIs.

Models:

- `TransactionCreate`
- `TransactionOut`
- `MonthlySummary`
- `CsvImportBody`
- `CsvImportOut`

Endpoints:

#### `POST /api/transactions`

Creates one transaction for current user.

#### `GET /api/transactions`

Lists all current user transactions ordered by date descending.

#### `GET /api/transactions/summary/monthly`

Takes `year` and `month`, returns total amount for that month.

#### `POST /api/transactions/import/csv`

Imports pasted CSV text.

Workflow:

1. Parse CSV with `csv.DictReader`.
2. Use configured column names.
3. Convert amount to `Decimal`.
4. Convert date with `date.fromisoformat`.
5. Insert rows up to `max_rows`.
6. Return imported/skipped counts and sample errors.

### `backend/app/api/routes/invoices.py`

PDF invoice API.

Models:

- `LineItem`
- `InvoicePdfBody`

Endpoint:

#### `POST /api/invoices/pdf`

Workflow:

1. Requires logged-in user.
2. Generates short invoice reference.
3. Uses current user email as `bill_to`.
4. Calls `build_invoice_pdf()`.
5. Returns PDF response with download headers.

### `backend/app/api/routes/agents.py`

Endpoint:

```text
GET /api/agents
```

Returns metadata about the three agents:

- Budget Planner
- Invoice Generator
- Investment Analyser

Useful for Swagger documentation and frontend agent selection.

### `backend/app/api/routes/chat.py`

Central chat endpoint.

Models:

- `ChatRequest`
- `ChatResponse`

Endpoint:

```text
POST /api/chat/message
```

Important helper functions:

#### `_normalized_tag(agent)`

Maps internal agent enum to response tag:

- budget planner -> `[AGENT: BUDGET]`
- invoice generator -> `[AGENT: INVOICE]`
- investment analyser -> `[AGENT: INVESTMENT]`

#### `_canonical_json_tail(agent)`

Returns fallback JSON tail for each agent if model/agent reply does not contain valid JSON.

#### `_enforce_reply_contract(reply, agent)`

Guarantees every reply has:

1. correct agent tag,
2. prose,
3. final JSON line.

This is important because both deterministic agents and LLM outputs can be inconsistent.

#### `_build_recent_context(db, user_id, turns=3)`

Fetches recent chat `MemoryChunk` rows and builds compact conversation context.

#### `_latest_onboarding_context(db, user_id)`

Fetches latest onboarding profile memory.

#### `_is_high_signal_user_message(text)`

Decides whether user message is important enough to store.

Signals include:

- digits,
- words like income, salary, budget, rent, stock, invoice, tax.

#### `_latest_assistant_agent(db, user_id)`

Finds the most recent assistant memory and infers which agent answered.

#### `_followup_agent_override(db, user_id, message)`

Keeps short follow-ups in the same investment flow when appropriate.

Example: after investment reply, a message like “how do I allocate this?” can stay with investment.

#### `_should_store_assistant_reply(reply)`

Avoids storing crisis-mode or generic low-value assistant replies.

#### `chat_message()`

Main request handler.

Workflow:

1. Search semantic memory.
2. Build recent chat context.
3. Get onboarding context.
4. Merge context blocks.
5. Apply follow-up override if needed.
6. Call `run_turn()`.
7. Enforce reply contract.
8. Add metadata.
9. Store user/assistant memory where appropriate.
10. Return response.

## 7. Agent Files Explained

### `backend/app/agents/__init__.py`

Package marker for the agents module.

### `backend/app/agents/types.py`

Defines shared agent types.

#### `AgentName`

Enum with:

- `BUDGET_PLANNER`
- `INVOICE_GENERATOR`
- `INVESTMENT_ANALYSER`

#### `AgentResult`

Dataclass returned by all agents.

Fields:

- `agent`
- `reply`
- `planned_steps`
- `metadata`

### `backend/app/agents/intent.py`

Hybrid intent classifier.

Important constants:

- `_BUDGET`: regex for budget/spending terms.
- `_INVOICE`: regex for invoice/billing terms.
- `_INVEST`: regex for stock/investment terms.
- `PROTOTYPES`: example phrases for each agent.

Functions:

#### `_keyword_vector(text)`

Counts keyword matches per agent and normalizes scores.

#### `_agent_centroids()`

Embeds prototype phrases and computes one centroid vector per agent. Cached with `lru_cache`.

#### `_embedding_vector(text)`

Embeds the user message and computes similarity to each agent centroid.

#### `classify_agent(user_message)`

Blends keyword and embedding scores:

```python
combined = (1 - w) * keyword_score + w * embedding_score
```

Returns the highest-scoring agent.

### `backend/app/agents/orchestrator.py`

Central agent runner.

Important functions:

#### `_compose_llm_user_message(user_message, rag_context)`

Combines retrieved context and user message before sending to the local LLM.

#### `run_turn(...)`

Main orchestrator.

Behavior:

- If local LLM is enabled and no forced agent is set, try local LLM first.
- If LLM works, determine agent from generated tag.
- If LLM fails or is disabled, classify and call deterministic specialist.
- Adds metadata such as `source: llm` or `source: rules`.

### `backend/app/agents/budget_planner.py`

Budget specialist.

Workflow:

1. Get today’s date.
2. Look back 30 days.
3. Read user transaction currency.
4. Aggregate transactions by category.
5. Compute net total.
6. Add month-over-month insights.
7. Add RAG context.
8. Build enriched message.
9. Try `generate()` from local FinMate model.
10. If generation fails, return deterministic fallback response.

Planned steps returned:

- `load_transactions_30d`
- `aggregate_by_category`
- `mom_insights`
- `retrieve_rag`
- `finmate_generate`

### `backend/app/agents/invoice_generator.py`

Invoice specialist.

Important regex:

```python
_AMOUNT_LINE = re.compile(r"^\s*([\d.,]+)\s+(.+?)\s*$", re.M)
```

This parses lines like:

```text
1200 Website design
400 SEO audit
```

Workflow:

1. Parse line items from user message.
2. Convert amounts to `Decimal`.
3. Ignore invalid or non-positive amounts.
4. Compute total.
5. Generate invoice reference.
6. Add RAG context.
7. Build enriched message.
8. Try local model generation.
9. If generation fails:
   - if items exist, return payload for `/api/invoices/pdf`,
   - otherwise ask user for line items.

Metadata includes:

- invoice reference,
- parsed item count,
- parsed total.

### `backend/app/agents/investment_analyser.py`

Investment specialist.

Main features:

- extracts stock tickers,
- maps company names to tickers,
- validates tickers with Yahoo Finance,
- fetches 3-month market history,
- computes last close,
- computes previous-day change,
- computes 20-day SMA,
- uses onboarding risk/location/income when available,
- provides fallback allocation suggestions.

Important helper functions:

#### `_extract_risk_from_context(ctx)`

Reads risk tolerance from onboarding context.

#### `_extract_income_from_context(ctx)`

Reads monthly income from onboarding context.

#### `_extract_location_from_context(ctx)`

Reads location from onboarding context.

#### `_extract_lump_sum(message)`

Extracts investment amount from text. Supports:

- plain numbers,
- `k`,
- `m`,
- `lakh`,
- `lakhs`.

#### `_allocation_for_risk(risk)`

Returns equity/debt/cash split:

- aggressive: `75/20/5`
- conservative: `40/45/15`
- moderate/default: `60/30/10`

#### `_plain_investment_plan(message, rag_context)`

Used when no ticker is found. Builds deterministic portfolio guidance from amount, risk, income, and location.

#### `_pick_tickers(message)`

Detects tickers from:

- `$AAPL`,
- known company names like Microsoft, Apple, Tesla,
- uppercase tokens after stopword filtering,
- Yahoo validation for unknown uppercase symbols.

#### `_analyze_symbol(symbol)`

Calls `yfinance.Ticker(symbol)` and computes:

- last close,
- change vs previous close,
- percent change,
- 20-day SMA,
- whether price is above/below SMA,
- session range,
- 52-week range.

#### `run(...)`

Main investment agent:

1. Detect tickers.
2. If no tickers, return allocation plan.
3. If tickers found, fetch market data.
4. Build response requirements.
5. Try model generation with investment-specific system instructions.
6. Normalize investment reply shape.
7. Return metadata with tickers.

## 8. ML And RAG Files Explained

### `backend/app/ml/__init__.py`

Package marker for ML modules.

### `backend/app/ml/embeddings.py`

Lazy-loads the sentence transformer.

Functions:

#### `get_sentence_model()`

Loads:

```text
sentence-transformers/all-MiniLM-L6-v2
```

Cached with `lru_cache`.

#### `encode_texts(texts)`

Returns normalized embeddings for a list of strings.

### `backend/app/ml/finmate.py`

Local LLM loader, generator, and postprocessor.

Important constants:

- `SYSTEM`: system prompt enforcing FinMate response format.
- `SYSTEM_EXTRA_INVESTMENT`: extra investment-specific instructions.
- `CRISIS_KEYWORDS`: triggers budget emergency response.
- `VALID_KEYS`: allowed JSON keys.

Important functions:

#### `_resolve_lora_root()`

Resolves configured LoRA path.

#### `_find_adapter_dir(root)`

Finds a directory containing:

- `adapter_model.safetensors`, or
- `adapter_model.bin`.

Prefers root directory, then checkpoint folders.

#### `_read_base_model_name(adapter_dir)`

Reads base model from `adapter_config.json`.

Fallback:

```text
Qwen/Qwen2.5-1.5B-Instruct
```

#### `_load_model()`

Loads:

- base causal LM,
- tokenizer,
- PEFT adapter.

Uses CUDA if available, otherwise CPU.

#### `_normalize_tools_needed()`

Cleans the `tools_needed` JSON field so it becomes short machine-readable tokens.

#### `_normalize_steps()`

Cleans the `steps` JSON field.

#### `_last_brace_object_span()`

Finds the last JSON-like object in model output.

#### `_parse_finmate_dict()`

Tries to parse model JSON. Has fallback regex extraction when malformed.

#### `_postprocess()`

Normalizes:

- malformed agent tags,
- bad JSON,
- invalid `tools_needed`,
- vague intents,
- missing steps.

#### `route_key_from_reply(text)`

Maps reply tags to internal route keys:

- `[AGENT: INVOICE]` -> `invoice_generator`
- `[AGENT: INVESTMENT]` -> `investment_analyser`
- otherwise -> `budget_planner`

#### `extract_planned_steps(text)`

Extracts `steps` array from final JSON line.

#### `llm_available()`

Checks whether LoRA adapter weights exist.

#### `ensure_investment_reply_shape(reply)`

Fixes investment replies that are missing tag/prose/valid JSON.

#### `ensure_budget_invoice_llm_reply_shape(reply)`

Fixes budget/invoice replies.

#### `finalize_llm_reply(reply)`

Routes generated reply to the correct fixer.

#### `generate(user_message, ...)`

Main local generation function.

Workflow:

1. Check crisis keywords.
2. Load model/tokenizer.
3. Build system + user prompt.
4. Apply chat template if tokenizer supports it.
5. Tokenize.
6. Generate with:
   - `max_new_tokens`
   - `repetition_penalty=1.15`
   - `do_sample=False`
7. Decode only new tokens.
8. Postprocess output.

#### `clear_model_cache()`

Clears cached loaded model.

### `backend/app/ml/finmate-lora/`

Contains trained local LoRA adapter artifacts.

Important files:

#### `adapter_config.json`

PEFT adapter configuration. Defines base model, LoRA rank, alpha, dropout, target modules, task type.

#### `adapter_model.safetensors`

Main LoRA adapter weights.

#### `tokenizer.json`

Tokenizer data.

#### `tokenizer_config.json`

Tokenizer configuration.

#### `chat_template.jinja`

Chat prompt template used by tokenizer/model.

#### `README.md`

Generated model card. States this is a fine-tuned version of `Qwen/Qwen2.5-1.5B-Instruct`.

#### `checkpoint-*` folders

Intermediate checkpoint directories. They contain adapter weights, tokenizer files, trainer state, RNG state, and configs. Runtime `_find_adapter_dir()` can fall back to checkpoint folders if root weights are absent.

### `backend/app/rag/memory_store.py`

Lightweight RAG memory layer.

Important functions:

#### `add_memory(db, user_id, content, source="chat")`

Stores a memory chunk in Postgres.

#### `search_memory(db, user_id, query, k=5, min_similarity=0.22)`

Workflow:

1. Fetch latest 200 memory chunks for user.
2. Embed query and memory texts.
3. Normalize vectors.
4. Compute cosine similarity.
5. Sort descending.
6. Return top-k chunks above threshold.

Important note: this is not using Chroma or pgvector. It uses Postgres as storage and local NumPy similarity search at request time.

## 9. Services And Invoice Files

### `backend/app/services/spending_insights.py`

Adds deterministic budget insight beyond LLM text.

Function:

#### `category_delta_vs_prior_month(db, user_id, ref=None)`

Compares last completed calendar month with the month before.

Workflow:

1. Determine last completed month.
2. Determine previous month.
3. Aggregate transaction totals by category for both.
4. Compute percent change.
5. Return readable month-over-month signals.

### `backend/app/invoice/pdf_invoice.py`

Generates invoice PDFs using ReportLab.

Function:

#### `build_invoice_pdf(invoice_ref, bill_to, line_items, currency="USD")`

Workflow:

1. Create in-memory PDF buffer.
2. Draw invoice title.
3. Draw invoice reference and bill-to email.
4. Draw line-item table.
5. Sum total.
6. Add new pages if the y-position gets too low.
7. Return raw PDF bytes.

## 10. Backend Scripts

### `backend/scripts/migrate_add_password_hash.sql`

Migration helper for older databases.

Adds:

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
```

### `backend/scripts/csv_seed_transactions.py`

CLI script for importing CSV data into the transactions table for one user.

Important functions:

#### `parse_date(raw)`

Supports multiple date formats and falls back to today.

#### `dec(raw)`

Safely converts strings to `Decimal`.

#### `detect_format(header)`

Detects supported CSV format.

#### `seed_tracker(user_id, row)`

Creates one transaction from tracker-style rows. Expenses are stored as negative values.

#### `seed_indian(user_id, row)`

Creates multiple category transactions from Indian spending rows.

#### `main()`

Parses CLI args, validates user, reads CSV, inserts or dry-runs transactions.

### `backend/scripts/evaluate_chat.py`

Evaluates a running backend against held-out prompts.

Important functions:

#### `_load_jsonl(path)`

Reads JSONL dataset rows.

#### `_is_format_compliant(reply)`

Checks:

- first line has valid agent tag,
- final line is JSON,
- JSON has required keys,
- `steps` is a list.

#### `evaluate(base_url, token, dataset)`

Calls `/api/chat/message` for each prompt and reports:

- routing accuracy,
- format compliance,
- failed rows.

### `backend/scripts/generate_eval_set.py`

Creates synthetic evaluation prompts.

Important functions:

#### `build_row(kind, rnd)`

Creates one synthetic prompt for:

- budget,
- invoice,
- investment.

#### `main()`

Generates balanced rows and writes JSONL.

## 11. Frontend Structure

```text
frontend/
  src/
    App.tsx
    main.tsx
    styles.css
    vite-env.d.ts
  index.html
  package.json
  package-lock.json
  tsconfig.json
  tsconfig.node.json
  tsconfig.tsbuildinfo
  vite.config.ts
```

## 12. Frontend Files Explained

### `frontend/package.json`

Defines frontend scripts:

```json
{
  "dev": "vite",
  "build": "tsc -b && vite build",
  "preview": "vite preview"
}
```

Dependencies:

- `react`
- `react-dom`

Dev dependencies:

- TypeScript
- Vite
- React plugin
- React type packages

### `frontend/index.html`

HTML shell.

Contains:

```html
<div id="root"></div>
<script type="module" src="/src/main.tsx"></script>
```

### `frontend/vite.config.ts`

Vite configuration.

Important behavior:

- Uses React plugin.
- Runs dev server on port `5173`.
- Proxies `/api` requests to backend:

```text
http://127.0.0.1:8000
```

### `frontend/tsconfig.json`

Strict TypeScript settings for frontend source.

Important flags:

- `strict: true`
- `noUnusedLocals: true`
- `noUnusedParameters: true`
- `jsx: react-jsx`

### `frontend/tsconfig.node.json`

TypeScript settings for Node-side config files such as `vite.config.ts`.

### `frontend/tsconfig.tsbuildinfo`

Generated TypeScript build cache. Not part of app logic.

### `frontend/src/vite-env.d.ts`

Vite type declarations.

### `frontend/src/main.tsx`

React entry point.

Workflow:

1. Imports React.
2. Imports ReactDOM.
3. Imports `App`.
4. Imports CSS.
5. Renders app inside `React.StrictMode`.

### `frontend/src/styles.css`

Global styling.

Defines:

- root font/colors/background,
- centered `.app` layout,
- `.panel` cards,
- labels,
- inputs,
- textarea,
- buttons,
- disabled button state,
- preformatted output wrapping.

### `frontend/src/App.tsx`

Main frontend component.

Important types:

#### `ChatResponse`

Matches backend chat response.

Fields:

- `agent`
- `reply`
- `planned_steps`
- `metadata`

#### `ChatTurn`

Represents local conversation history.

Fields:

- `id`
- `role`
- `text`
- `agent`

Important constants:

#### `AGENTS`

Dropdown options:

- Auto hybrid routing
- Budget Planner
- Invoice Generator
- Investment Analyser

#### `TOKEN_KEY`

Browser localStorage key:

```text
finmate_token
```

Important helper:

#### `authHeaders(token)`

Builds JSON headers and adds bearer token when present.

Important state:

- `token`: JWT from localStorage.
- `email`, `password`, `displayName`: auth form.
- `agent`: selected forced agent.
- `message`: chat message.
- onboarding fields.
- `csvText`: transaction import text.
- `reply`: latest chat response.
- `history`: displayed conversation.
- `error`: error output.
- `loading`: UI loading state.

Important functions:

#### `cleanAssistantText(raw)`

Removes:

- `[AGENT: ...]` tag lines,
- final JSON line.

This lets the UI show only natural language.

#### `logout()`

Clears token and chat state.

#### `register()`

Calls:

```text
POST /api/auth/register
```

Stores returned JWT in localStorage.

#### `login()`

Calls:

```text
POST /api/auth/login
```

Stores returned JWT.

#### `send()`

Calls:

```text
POST /api/chat/message
```

Workflow:

1. Validate token.
2. Add user turn to local history.
3. Send message and optional forced agent.
4. Store backend reply.
5. Add assistant turn to local history.

#### `downloadSamplePdf()`

Calls:

```text
POST /api/invoices/pdf
```

With sample line items:

- Consulting
- Hosting

Then creates a browser download link.

#### `saveOnboarding()`

Calls:

```text
POST /api/users/onboarding
```

Converts comma-separated goals into an array and saves profile.

#### `importCsvTransactions()`

Calls:

```text
POST /api/transactions/import/csv
```

Displays import result in conversation history.

Rendered sections:

- app title,
- auth panel,
- logged-in actions,
- agent dropdown,
- onboarding panel,
- CSV import panel,
- chat panel,
- conversation history,
- error panel,
- latest reply details.

## 13. Root Files Explained

### `README.md`

Setup and usage guide.

Documents:

- PostgreSQL startup,
- backend startup,
- frontend startup,
- auth smoke tests,
- CSV seed script,
- chat evaluation,
- project layout,
- recommended next steps.

### `docker-compose.yml`

Defines PostgreSQL service:

```yaml
image: postgres:16-alpine
POSTGRES_USER: finmate
POSTGRES_PASSWORD: finmate
POSTGRES_DB: finmate
ports:
  - "5432:5432"
```

Uses named volume:

```text
finmate_pg
```

### `package.json`

Root convenience scripts.

Scripts:

#### `npm run dev`

Runs backend and frontend together using `concurrently`.

#### `npm run dev:api`

Starts FastAPI:

```text
cd backend && python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

#### `npm run dev:web`

Starts Vite frontend:

```text
cd frontend && npm run dev
```

### `package-lock.json`

Pinned dependency tree for root Node dependencies.

### `.gitignore`

Git ignore rules for generated files, dependencies, environments, and local artifacts.

### `bash.exe.stackdump`

Crash dump from a shell process. It is not used by the application.

## 14. API Summary

### Public/Basic

```text
GET /
GET /api/health
```

### Auth

```text
POST /api/auth/register
POST /api/auth/login
```

### User

```text
GET /api/users/me
POST /api/users/onboarding
GET /api/users/onboarding/latest
```

### Transactions

```text
POST /api/transactions
GET /api/transactions
GET /api/transactions/summary/monthly
POST /api/transactions/import/csv
```

### Invoices

```text
POST /api/invoices/pdf
```

### Agents

```text
GET /api/agents
```

### Chat

```text
POST /api/chat/message
```

## 15. Data Flow By Feature

### 15.1 Register/Login

```text
Frontend form
  -> /api/auth/register or /api/auth/login
  -> password hash / password verify
  -> JWT creation
  -> frontend localStorage
```

### 15.2 Onboarding

```text
Frontend onboarding form
  -> /api/users/onboarding
  -> MemoryChunk(source="onboarding")
  -> later injected into chat context
```

### 15.3 Chat

```text
Frontend message
  -> /api/chat/message
  -> auth
  -> memory retrieval
  -> recent context
  -> onboarding context
  -> orchestrator
  -> agent
  -> reply contract enforcement
  -> memory write
  -> frontend display
```

### 15.4 Budget Advice

```text
Chat message
  -> budget_planner
  -> load last 30 days transactions
  -> aggregate by category
  -> month-over-month comparison
  -> optional LLM generation
  -> fallback if needed
```

### 15.5 Investment Advice

```text
Chat message
  -> investment_analyser
  -> extract tickers/company names
  -> yfinance market lookup
  -> compute last close/change/SMA
  -> optional LLM generation
  -> deterministic allocation fallback if no ticker
```

### 15.6 Invoice PDF

```text
Frontend sample PDF button or direct API call
  -> /api/invoices/pdf
  -> build_invoice_pdf()
  -> PDF bytes
  -> browser download
```

### 15.7 CSV Import

```text
Frontend CSV textarea
  -> /api/transactions/import/csv
  -> csv.DictReader
  -> Decimal/date parsing
  -> Transaction rows
  -> import result
```

## 16. Important Implementation Notes

1. The backend currently uses `Base.metadata.create_all()` instead of a full migration framework.
2. The `Budget` table exists but does not yet have complete CRUD API routes.
3. The frontend text says “RAG memory (Chroma)”, but the actual backend uses Postgres plus local embedding similarity.
4. Local LLM inference is optional and disabled by default.
5. The LoRA adapter depends on the base model being available locally or downloadable.
6. The investment agent depends on Yahoo Finance availability through `yfinance`.
7. Assistant replies are intentionally machine-readable because each response ends with JSON.
8. The invoice agent prepares invoice guidance, while actual PDF generation is handled by `/api/invoices/pdf`.
9. Conversation memory is selective: not every message is stored.
10. The app is designed as a capstone/demo scaffold but already has real auth, database persistence, routing, memory, and PDF generation.

## 17. Recommended Report Description

FinMate is a multi-agent personal finance assistant. The backend authenticates users with JWT, stores users, transactions, and memory in PostgreSQL, retrieves relevant financial context with sentence-transformer embeddings, and routes each chat message to a budget, invoice, or investment specialist. The budget agent uses transaction aggregates and spending insights, the investment agent uses ticker detection and Yahoo Finance market data, and the invoice flow supports PDF generation. The system can optionally use a local Qwen2.5 LoRA fine-tuned model for structured replies, but by default it runs through deterministic specialist agents with fallback responses.

