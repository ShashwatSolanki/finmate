# FinMate reproducible evaluation package

This directory is for research evaluation, not financial advice. Use a disposable local PostgreSQL database and a dedicated synthetic user only. Never use \`--reset-test-user-state\` against production or a real account.

## Files
- Run \`python research/evaluation/generate_benchmark.py\` to create \`benchmark.jsonl\`: 120 synthetic pilot tasks (40 numerical, 40 memory—including 8 absent-memory abstention cases—and 40 routing/orchestration).
- \`run_benchmark.py\`: API and direct-local-Qwen runner, saves raw case-level outputs and a \`.manifest.json\`.
- \`summarize_results.py\`: rates, bootstrap confidence intervals, paired comparisons and human unsupported-claim annotation template.
- \`test_*.py\`: unit tests for scoring helpers and generated case integrity.

The generated corpus is a pilot, not a validated gold-standard benchmark. Manually review all labels and expected values, freeze it before final runs, and record commit/model/hardware details.

## Existing tests versus research metrics
The existing \`backend/scripts/evaluate_ai.py\` mainly checks response format, routing, confidence metadata, RAG metadata, bounded specialist order and invoice-artifact presence. It does not grade financial answer correctness. The prior \`backend/scripts/evaluate_rag.py\` uses mocked embeddings and must not be presented as production retrieval performance. This package adds gold-number scoring, structured invoice checks for the 10 synthetic invoice-subtotal cases, relevant-memory rank, absent-memory abstention, and latency.

## Ablation conditions
Set these variables before restarting the API:
- Full: \`FINMATE_USE_RAG=true FINMATE_AGENTIC_MODE=true FINMATE_USE_LLM=true\`
- No semantic retrieval: \`FINMATE_USE_RAG=false\`
- No multi-agent planner: \`FINMATE_AGENTIC_MODE=false\`
- No LLM generation: \`FINMATE_USE_LLM=false\`
- Direct local model: \`--condition llm_only\`, which bypasses API tools, memory and specialist orchestration.

The branch introduces \`FINMATE_USE_RAG\`, defaulting to true. Disabling it does not disable recent-conversation or onboarding context by itself. The runner clears test-user state and seeds memory rows with source \`research_eval\`; use only a disposable user and local database. The direct-model baseline is not equivalent to an external general-purpose LLM, and this limitation should be stated.

## Safe setup and commands
1. Use an isolated development database, not staging/production.
2. Create a synthetic evaluation user on that DB and obtain its UUID and valid access token.
3. Install backend requirements. Model conditions need the local Qwen adapter files.
4. From \`backend/\`, run an API condition:
\`\`\`bash
python ../research/evaluation/generate_benchmark.py
python ../research/evaluation/run_benchmark.py --condition full \\
  --token "$FINMATE_TEST_TOKEN" --user-id "$FINMATE_TEST_USER_ID" \\
  --reset-test-user-state --commit "<tested-git-sha>" \\
  --environment-notes "backend flags: FINMATE_USE_RAG=true, FINMATE_AGENTIC_MODE=true, FINMATE_USE_LLM=true; API host/hardware: <details>" \\
  --output ../research/evaluation/results/full.jsonl
\`\`\`
For PowerShell, use \`$env:FINMATE_TEST_TOKEN\` and \`$env:FINMATE_TEST_USER_ID\`.

**Destructive warning:** before every case, the runner deletes the selected user's chat sessions, memory, transactions, budgets and investment holdings, then seeds synthetic fixtures. This is required for isolation and must only be used on a dedicated disposable test account and database. Do not put tokens or database credentials in reports.

Direct Qwen baseline:
\`\`\`bash
python ../research/evaluation/run_benchmark.py --condition llm_only \\
  --commit "<tested-git-sha>" --environment-notes "adapter revision and hardware" \\
  --output ../research/evaluation/results/llm_only.jsonl
\`\`\`
Use \`--limit 20\` only for a pipeline/debug pilot, not final results.

## Summarize
Supply every completed condition in a stable order; the first file is the reference condition:
\`\`\`bash
python ../research/evaluation/summarize_results.py \\
  research/evaluation/results/full.jsonl \\
  research/evaluation/results/no_rag.jsonl \\
  research/evaluation/results/no_agentic.jsonl \\
  research/evaluation/results/no_llm.jsonl \\
  research/evaluation/results/llm_only.jsonl \\
  --compare --out-dir research/evaluation/summary
\`\`\`
For those paths, run the summarizer from repository root, or adjust paths consistently. Outputs are \`summary.md\`, \`summary.json\`, and \`claims_annotation_template.csv\`.

## Metrics
- Request success/failure, specialist routing and agent-sequence correctness
- Gold-number accuracy, transaction total correctness, structured invoice currency/line amounts/subtotal
- Personal-memory correctness and appropriate abstention when the fact is not stored
- Task completion (all applicable checks must pass)
- Retrieval Hit@5 and MRR with actual runtime embeddings
- Mean/median/p95 latency and per-group results
- Unsupported factual-claim rate: requires independent human annotation with a defined rubric; the script makes a blank annotation template rather than inventing a score.

Report denominators for each metric. Preserve JSONL results and manifests. For API conditions, the manifest cannot inspect the separate backend process; record actual server settings and machine details in \`--environment-notes\`. Keep live market data out of gold correctness cases unless results are timestamped and cached.

## Before publication
1. Independently validate every generated task and gold label; 120 templates are a pilot, not a representative user study.
2. Confirm category and amount conventions against the tested commit and inspect the actual payload format.
3. Consider external FinQA/ConvFinQA/FinanceBench evaluation only as complementary tasks with separate metrics/licence checks.
4. Have two reviewers annotate a subset of factual claims and report agreement.
5. Do not claim calibrated confidence from FinMate's heuristic without a separate calibration test.
