# FinMate reproducible evaluation package

This directory is for research evaluation, not financial advice. Use a dedicated disposable local PostgreSQL database and a fresh synthetic evaluation identity only. Never use the state-reset option against staging, production, or a real user's account.

## Files

- Run **generate_benchmark.py** to create **benchmark.jsonl**: 120 synthetic pilot tasks (40 numerical, 40 memory—including 8 absent-memory abstention cases—and 40 routing/orchestration).
- **run_benchmark.py**: API and direct-local-Qwen runner; saves raw case-level outputs and a manifest.
- **summarize_results.py**: rates, bootstrap confidence intervals, paired comparisons, and a human unsupported-claim annotation template.
- **test_*.py**: unit tests for scoring helpers, database-safety checks, and generated-case integrity.

The generated corpus is a pilot, not a validated gold-standard benchmark. Manually review all labels and expected values, freeze the dataset before final runs, and record commit/model/hardware details.

## Existing tests versus research metrics

The existing backend/scripts/evaluate_ai.py mainly checks response format, routing, confidence metadata, RAG metadata, bounded specialist order, and invoice-artifact presence. It does not grade financial answer correctness. The prior backend/scripts/evaluate_rag.py uses mocked embeddings and must not be presented as production retrieval performance. This package adds gold-number scoring, structured invoice checks for the 10 synthetic invoice-subtotal cases, relevant-memory rank, absent-memory abstention, and latency.

## Ablation conditions

Set these variables before restarting the API:

- Full: FINMATE_USE_RAG=true, FINMATE_AGENTIC_MODE=true, FINMATE_USE_LLM=true
- No semantic retrieval: FINMATE_USE_RAG=false
- No multi-agent planner: FINMATE_AGENTIC_MODE=false
- No LLM generation: FINMATE_USE_LLM=false
- Direct local model: --condition llm_only, which bypasses API tools, memory, and specialist orchestration.

FINMATE_USE_RAG defaults to true. Disabling it does not disable recent-conversation or onboarding context by itself. The runner clears test-user state and seeds memory rows with source research_eval; use only a dedicated disposable database. The direct-model baseline is not equivalent to an external general-purpose LLM, and this limitation should be stated.

## Safe setup and commands

1. Use an isolated local database whose name includes eval or test (for example, finmate_eval). The runner refuses to perform destructive resets against other database names.
2. Use a fresh UUID for the evaluation user. The runner creates a synthetic, non-login user record in the isolated database when that UUID is not present, and mints a short-lived local access token if --token is omitted.
3. Set the same DATABASE_URL and JWT_SECRET configuration for the API server and the runner. Never copy an access token into chat, reports, or source control.
4. Install backend requirements. Model conditions need the local Qwen adapter files.
5. Start the API with the intended research flags, then run the API pilot from the backend directory. On PowerShell:

~~~powershell
$env:DATABASE_URL = "postgresql+psycopg2://finmate:finmate@127.0.0.1:5433/finmate_eval"
$env:FINMATE_TEST_USER_ID = [guid]::NewGuid().ToString()
python ../research/evaluation/generate_benchmark.py
python ../research/evaluation/run_benchmark.py --condition full --pilot --user-id "$env:FINMATE_TEST_USER_ID" --reset-test-user-state --output ../research/evaluation/results/full-pilot.jsonl
~~~

Set DATABASE_URL to that same evaluation database in the separate terminal where Uvicorn starts. Keep JWT_SECRET identical in both processes; otherwise, the locally minted token will not validate. The runner will generate a fresh access token unless you explicitly pass --token. A stratified pilot contains 21 cases; omit --pilot for the complete 120-case dataset.

**Destructive warning:** before every case, the runner deletes the selected user's chat sessions, memory, transactions, budgets, and investment holdings, then seeds synthetic fixtures. This is required for isolation. The database-name guard is a backstop, not a substitute for checking that the database is disposable. Do not put tokens or database credentials in reports.

## Direct Qwen baseline

~~~powershell
python ../research/evaluation/run_benchmark.py --condition llm_only --commit "<tested-git-sha>" --environment-notes "adapter revision and hardware" --output ../research/evaluation/results/llm_only.jsonl
~~~

Use --limit only for a pipeline/debug pilot, not final results.

## Summarize

Run the summarizer from the repository root after collecting every completed condition. Keep the reference condition first:

~~~bash
python research/evaluation/summarize_results.py research/evaluation/results/full-pilot.jsonl research/evaluation/results/no_rag.jsonl research/evaluation/results/no_agentic.jsonl research/evaluation/results/no_llm.jsonl research/evaluation/results/llm_only.jsonl --compare --out-dir research/evaluation/summary
~~~

Outputs are summary.md, summary.json, and claims_annotation_template.csv.

## Correctness guardrails added during pilot debugging

The full API pilot exposed cases where the general LLM path answered budget questions without calling the data-backed budget specialist. Budget classification now routes through the specialist, and explicitly itemized income-minus-expenses questions plus category-specific transaction totals use deterministic calculations. This prevents model-generated guesses from being scored as account data. Other budget requests may still use the LLM to explain database-derived summaries.

Explicit saved-profile fact lookups are answered only when the requested value is present in retrieved profile context. If the requested value is absent, FinMate abstains rather than inventing it. This is a retrieval-grounded extraction path, not evidence that semantic retrieval always finds the correct memory; Hit@5/MRR must still be reported independently.

The bounded planner recognizes "investable surplus" as an investment signal so three-domain requests can plan the investment specialist when appropriate. Regression tests cover exact arithmetic, category selection, saved-profile facts, missing-memory abstention, and bounded multi-agent planning. Re-run the pilot after restarting the API, then inspect every failed JSONL record before running ablations. Do not loosen gold labels or scoring checks merely to increase completion rates.

## Metrics

- Request success/failure, specialist routing, and agent-sequence correctness
- Gold-number accuracy, transaction-total correctness, and structured invoice currency/line amounts/subtotal
- Personal-memory correctness and appropriate abstention when the fact is not stored
- Task completion (all applicable checks must pass)
- Retrieval Hit@5 and MRR with actual runtime embeddings
- Mean/median/p95 latency and per-group results
- Unsupported factual-claim rate: requires independent human annotation with a defined rubric; the script creates a blank annotation template rather than inventing a score.

Report denominators for each metric. Preserve JSONL results and manifests. For API conditions, the manifest cannot inspect the separate backend process; record actual server settings and machine details in --environment-notes. Keep live market data out of gold-correctness cases unless results are timestamped and cached.

## Before publication

1. Independently validate every generated task and gold label; 120 templates are a pilot, not a representative user study.
2. Confirm category and amount conventions against the tested commit and inspect the actual payload format.
3. Consider external FinQA/ConvFinQA/FinanceBench evaluation only as complementary tasks with separate metrics and licence checks.
4. Have two reviewers annotate a subset of factual claims and report agreement.
5. Do not claim calibrated confidence from FinMate's heuristic without a separate calibration test.
