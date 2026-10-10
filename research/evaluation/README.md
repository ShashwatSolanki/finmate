# FinMate reproducible evaluation package

This package is for a research study, not financial advice. Use only an isolated development database and a dedicated synthetic test account. Never run the reset option against production or a user's real account.

## Included
- `benchmark.jsonl`: generated 120-case synthetic pilot dataset: 40 numerical/data-grounded, 40 memory-dependent, and 40 routing/orchestration tasks.
- `generate_benchmark.py`: deterministic task generator.
- `run_benchmark.py`: live-API or direct local Qwen2.5+LoRA runner with per-case scores and raw response capture.
- `summarize_results.py`: descriptive scores, 95% bootstrap intervals, paired differences, and a human annotation CSV for factual-support review.

The generated case templates are a starting pilot, not independently validated ground truth. The team must review all labels and expected values, freeze the benchmark before the final runs, and record the exact code commit, runtime setup and model/adapter details.

## What existing tests measure
`backend/scripts/evaluate_ai.py` primarily measures routing, response-contract compliance, confidence metadata, retrieval-use metadata, bounded agent execution and invoice-artifact presence. It does not directly judge financial answer correctness. `backend/scripts/evaluate_rag.py` uses mocked embeddings to validate a small retrieval fixture; its scores must not be reported as real retrieval performance. This package adds gold-answer scoring and retrieval rank measurement using the configured embedding implementation.

## Ablation settings
Set variables before restarting the API for each condition:
- Full FinMate: `FINMATE_USE_RAG=true`, `FINMATE_AGENTIC_MODE=true`, `FINMATE_USE_LLM=true`
- No semantic RAG: `FINMATE_USE_RAG=false`, keep other values as full
- No multi-agent planning: `FINMATE_USE_RAG=true`, `FINMATE_AGENTIC_MODE=false`, `FINMATE_USE_LLM=true`
- No LLM (deterministic/fallback path): `FINMATE_USE_RAG=true`, `FINMATE_AGENTIC_MODE=true`, `FINMATE_USE_LLM=false`
- Direct model baseline: `--condition llm_only` invokes `app.ml.finmate.generate()` without API tools, memory or orchestration.

Restart the backend after changing flags. The runner records the requested condition label but cannot inspect the flags in a separately running API process. Save the exact environment flag snapshot with each run.

## Safe setup and run
1. Use a disposable local PostgreSQL database created specifically for evaluation.
2. Create a synthetic evaluation user; obtain its UUID and valid API access token.
3. Install backend dependencies and confirm the model adapter exists for model runs.
4. Ensure the API and runner point at the same isolated database.
5. From `backend/`, run an API condition:
```bash
python ../research/evaluation/run_benchmark.py --condition full \
  --token "$FINMATE_TEST_TOKEN" --user-id "$FINMATE_TEST_USER_ID" \
  --reset-test-user-state --output ../research/evaluation/results/full.jsonl
```
On PowerShell, use `$env:FINMATE_TEST_TOKEN` and `$env:FINMATE_TEST_USER_ID`.

**Destructive reset:** `--reset-test-user-state` deletes that user's chat sessions, memory, transactions, budgets and investment holdings before every case, then seeds the case's synthetic fixtures. Use it only with a disposable test account/database.

Direct local model baseline:
```bash
python ../research/evaluation/run_benchmark.py --condition llm_only \
  --output ../research/evaluation/results/llm_only.jsonl
```
Use `--limit 20` only for debugging the pilot—not as final results.

## Summarize and compare
```bash
python ../research/evaluation/summarize_results.py \
  ../research/evaluation/results/full.jsonl \
  ../research/evaluation/results/no_rag.jsonl \
  ../research/evaluation/results/no_agentic.jsonl \
  ../research/evaluation/results/no_llm.jsonl \
  ../research/evaluation/results/llm_only.jsonl \
  --compare --out-dir ../research/evaluation/summary
```
Outputs: `summary.md`, `summary.json`, and `claims_annotation_template.csv`. Bootstrap intervals quantify test-set sampling variation; they do not remove dataset bias or imply real-world representativeness.

## Metrics
- Request success and failure rate
- Specialist routing accuracy
- Gold-number accuracy with a predeclared tolerance
- Correct use of memory-dependent facts
- Correct specialist sequence for multi-domain tasks
- Presence of invoice artifacts (inspect the structured payload and totals separately before publication)
- Task completion, for all applicable checks
- Retrieval Hit@5 and MRR, computed with the real embedding model against labelled relevant chunks
- Mean/median/p95 latency
- Unsupported factual claims using an independent human annotation rubric, not fabricated automatic scores

For every metric report its eligible denominator. Preserve raw results and record git commit, Python/runtime version, model/adapter id, OS/hardware, environment flags, timestamps and external-data snapshots. Avoid live Yahoo Finance quotes in the main accuracy benchmark unless the returned responses and timestamps are cached.

## Before publication
1. Manually validate every synthetic case and gold label; deterministic generation does not establish independent annotation.
2. Verify the transaction category fixture behavior against the actual checked-out backend and re-run the pilot after any changes.
3. Score invoice line items and subtotal/tax values from `invoice_payload`, not just artifact metadata.
4. Freeze market-data snapshots or constrain the primary benchmark to requests that do not require live quotes.
5. Treat FinMate confidence as a heuristic—not a calibrated probability—unless separately calibrated.
6. Independently annotate unsupported claims; double-review a subset and report agreement.
7. The direct local-model baseline shares the model adapter but not necessarily the exact prompt budget or task tools. State this limitation clearly.
