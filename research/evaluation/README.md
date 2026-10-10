# FinMate reproducible evaluation package

This directory is for research evaluation, not financial advice. Use a dedicated disposable local PostgreSQL database and a fresh synthetic evaluation identity only. Never use the state-reset option against staging, production, or a real user's account.

## Files

- Run **generate_benchmark.py** to create 120 synthetic pilot tasks (40 numerical, 40 memory—including 8 absent-memory abstention cases—and 40 routing/orchestration). If `benchmark.jsonl` already exists, the generator refuses to overwrite it unless you supply `--output`; use a distinct filename to preserve older local datasets and compare their SHA-256 manifests.
- **run_benchmark.py**: API and direct-local-Qwen runner; saves raw case-level outputs and a manifest.
- **summarize_results.py**: rates, bootstrap confidence intervals, paired comparisons, and a human unsupported-claim annotation template.
- **test_*.py**: unit tests for scoring helpers, database-safety checks, and generated-case integrity.

The generated corpus is a pilot, not a validated gold-standard benchmark. Manually review all labels and expected values, freeze the dataset before final runs, and record commit/model/hardware details. Numeric, memory, and abstention correctness are scored from user-visible reply prose rather than the hidden JSON tail or internal metadata; invoice structure is validated separately from the exported payload. Execution-path accuracy is scored separately from the returned agent label using the trusted `metadata.execution_path` field (`specialist`, `general_llm`, `profile_memory`, or `agentic`). General educational prompts are expected to use the general-model path and do not receive specialist-route credit.

## Existing tests versus research metrics

The existing backend/scripts/evaluate_ai.py mainly checks response format, routing, confidence metadata, RAG metadata, bounded specialist order, and invoice-artifact presence. It does not grade financial answer correctness. The prior backend/scripts/evaluate_rag.py uses mocked embeddings and must not be presented as production retrieval performance. This package adds gold-number scoring from visible answer prose, structured invoice checks for currency/line amounts/subtotal/final total, relevant-memory rank, absent-memory abstention, specialist-execution-path validation, and latency. Transaction-spending fixtures use negative debit amounts in line with the transactions API convention; category spend is computed from negative expense entries as positive magnitudes.

## Ablation conditions

Set these variables before restarting the API:

- Full: FINMATE_USE_RAG=true, FINMATE_AGENTIC_MODE=true, FINMATE_USE_LLM=true
- No semantic retrieval: FINMATE_USE_RAG=false
- No multi-agent planner: FINMATE_AGENTIC_MODE=false
- No LLM generation: FINMATE_USE_LLM=false
- Deterministic agentic synthesis: FINMATE_AGENTIC_SYNTHESIS=false (default). This skips only the final synthesis model call; specialist execution and their own applicable paths remain enabled. Set FINMATE_AGENTIC_SYNTHESIS=true only when explicitly testing final synthesis.
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
python ../research/evaluation/generate_benchmark.py --output ../research/evaluation/benchmark-current.jsonl
python ../research/evaluation/run_benchmark.py --condition full --pilot --user-id "$env:FINMATE_TEST_USER_ID" --reset-test-user-state --dataset ../research/evaluation/benchmark-current.jsonl --output ../research/evaluation/results/full-pilot.jsonl
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

Income and investment amount parsers must require explicit labels instead of treating every currency-prefixed number as salary or investable capital. Deterministic balance arithmetic must also abstain when any additional numeric amount cannot be mapped to a supported expense label, even when that amount has no currency marker. This is particularly important for multi-domain prompts containing invoice line amounts alongside budget or investment requests; an amount alone does not establish what the amount represents.

Natural-language invoice requests can contain multiple line items joined by "plus". Regression coverage verifies that "configuration INR 400 plus support INR 500" is represented as two line items totaling INR 900, even when the invoice is requested in a longer multi-domain prompt. The answer's HTTP success or presence of an invoice export payload is not enough: item descriptions, amounts, currency, subtotal, and total must match the requested invoice.

The investment planner may use a moderate-risk split as an illustrative default when no risk preference is available, but must not state that the user has a saved moderate-risk profile unless that preference is present in retrieved profile context. Keep semantic correctness separate from task completion and latency, and record unsupported personalization claims as correctness failures.

Explicit saved-profile fact lookups are answered only when the requested value is present in retrieved profile context. If the requested value is absent, FinMate abstains rather than inventing it. This is a retrieval-grounded extraction path, not evidence that semantic retrieval always finds the correct memory; Hit@5/MRR must still be reported independently.

The memory correctness scorer evaluates the user-visible reply, not metadata. Numeric saved facts are parsed as numeric tokens using the case tolerance, so grouped formatting such as `INR 46,500` matches gold value `46500` without accepting a partial substring in a different number. This corrects a scoring-format false negative; it does not change gold labels or relax the correctness requirement.

The bounded planner recognizes "investable surplus" as an investment signal so three-domain requests can plan the investment specialist when appropriate. Regression tests cover exact arithmetic, category selection, saved-profile facts, missing-memory abstention, and bounded multi-agent planning. Re-run the pilot after restarting the API, then inspect every failed JSONL record before running ablations. Do not loosen gold labels or scoring checks merely to increase completion rates.

## Local inference performance diagnostics

The model logs stage timings for model loading or cache lookup, prompt preparation,
generation, decoding, and postprocessing, along with the execution device and token
counts. CUDA generation timings synchronize the GPU before measuring completion.
These diagnostics do not log prompts, replies, or financial content. The timing line
is logged at info level, leaving warning logs for actionable problems.

When recent transaction history is empty, the budget planner uses its deterministic
no-data fallback instead of invoking local model generation to restate the absence
of transactions. The LLM-backed path remains enabled when transaction category data
is present.

## Synthesis comparison note (2026-10-10)

On the 21-case stratified pilot, synthesis disabled and enabled both recorded 21/21 HTTP successes and 21/21 automated task completions. Invoice payload correctness was 2/2 in both result files. With synthesis disabled, multi-agent mean latency was 0.036 seconds (max 0.038 seconds); with synthesis enabled, it was 50.197 seconds (max 64.632 seconds), and p95 latency across all cases rose from 0.0543 seconds to 57.2059 seconds. In the reviewed multi-agent outputs, enabling synthesis did not materially change the returned answer: the deterministic fallback text was returned, while invoice references naturally differed between runs.

The synthesis prompt previously requested only the primary agent tag on the first line, while the acceptance guard required a tag for every specialist. The prompt was aligned with the acceptance contract and a mocked regression test verifies the intended format, but the follow-up live three-agent smoke still took 44.351 seconds and returned the deterministic fallback. Both the synthesis-off and synthesis-on pilot had 21/21 HTTP successes and task completions, but synthesis-on multi-agent mean latency was 50.197 seconds versus 0.036 seconds off, with no material answer improvement in reviewed outputs. Therefore final synthesis is opt-in and defaults to false. Do not enable it by default until diagnostics show that generated replies are accepted and improve answer quality at an acceptable latency.


## Latest reproducible pilot (2026-10-11)

A fresh 21-case stratified API pilot was run against application commit `54b3fc0147637e91ff435857922a3e8031c3861a` using condition `full`, a fresh synthetic user, and the dedicated disposable `finmate_eval` PostgreSQL database. Server flags were `FINMATE_USE_RAG=true`, `FINMATE_AGENTIC_MODE=true`, `FINMATE_USE_LLM=true`, and `FINMATE_AGENTIC_SYNTHESIS=false`.

- HTTP success: **21/21**
- Automated task completion: **21/21**
- Runner-reported failed cases: **0**
- Dataset used: local `research/evaluation/benchmark-final-review.jsonl`
- Raw results: local `research/evaluation/results/final-pilot-20261011-021636.jsonl`
- Manifest: local `research/evaluation/results/final-pilot-20261011-021636.manifest.json`

The raw results, manifest, and generated dataset are local evaluation artifacts and are not committed by this documentation update; preserve them for audit and reproducibility. This pilot exercised the scoring checks configured for its 21 selected cases. It is not a human audit of every claim and does not establish population-level financial correctness. Two non-fatal environment warnings appeared: a `RequestsDependencyWarning` about installed HTTP-related dependency versions and a Transformers warning that `TRANSFORMERS_CACHE` is deprecated in favor of `HF_HOME`. Neither stopped the run.

## Metrics

- Request success/failure, specialist routing, and agent-sequence correctness
- Gold-number accuracy from user-visible replies, transaction-total correctness, and structured invoice currency/line amounts/subtotal/final total
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


## Baseline audit and benchmark versioning (2026-10-11)

The original local `benchmark-final-review.jsonl` is preserved as the version-1 baseline dataset. Its SHA-256 was `6757ca2f715450d687a69ff04913a56408a877db83049ff47ee9c4455aebc127`; the recorded full-system run on application commit `4a3f0096c573d1d85920792582c733e542da6b35` returned 120/120 HTTP successes and 117/120 automated task completions. Do not overwrite its raw output or manifest.

Review of that run found one invoice task whose prompt requested an invoice without stating line items while the scorer required a structured artifact; its expected behavior was therefore underspecified. The revised generator gives that task two explicit line items and gold descriptions/amounts. The same run also exposed planner vocabulary gaps for “cautious allocation for excess funds” and “household costs”. Regression tests and bounded-planner phrase handling have been added for those cases.

Generate a separately named dataset after checking out the reviewed planner/benchmark revision, for example:

~~~bash
python research/evaluation/generate_benchmark.py --output research/evaluation/benchmark-reviewed-v2.jsonl
~~~

Record and freeze the resulting SHA-256 before running conditions. Preserve version-1 artifacts unchanged. For a controlled ablation comparison, use the same reviewed-v2 file and the same application commit for every condition; rerun the full condition on that version before making a primary result claim. The version-1 run remains a development baseline, not a holdout evaluation or a validated gold-standard benchmark.
