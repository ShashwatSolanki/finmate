# AI & ML System

## 1. Purpose

FinMate uses a small local language model together with deterministic specialist agents.

The AI system is intentionally designed so that financial operations do not depend entirely on generated text.

## 2. Base model

The runtime adapter is based on:

```text
Qwen/Qwen2.5-1.5B-Instruct
```

The project uses PEFT LoRA adapters.

Current adapter configuration includes:

| Parameter | Value |
|---|---:|
| LoRA rank | 16 |
| LoRA alpha | 32 |
| LoRA dropout | 0.05 |
| Target modules | q_proj, v_proj |
| Task | Causal language modeling |

Runtime model files live under:

```text
backend/app/ml/finmate-lora/
```

## 3. Runtime inference

The main implementation is:

```text
backend/app/ml/finmate.py
```

The loader:

1. resolves the configured adapter directory
2. reads `adapter_config.json`
3. identifies the base model
4. loads the tokenizer
5. loads the base causal language model
6. attaches the PEFT adapter
7. applies the tokenizer chat template when available
8. generates with sampling disabled
9. post-processes the output
10. enforces the FinMate response shape

The adapter is optional. When model loading or generation fails, deterministic specialist logic remains available.

## 4. Training assets

Training-related material lives under:

```text
training/
├── data/
├── scripts/
└── colab/
```

The training pipeline prepares instruction-style JSONL data and the QLoRA notebook is used for supervised fine-tuning.

The runtime documentation intentionally does not duplicate the complete training notebook. Training implementation details belong with the training assets.

## 5. Why a small model

The 1.5B model is suitable for a local/demo-oriented application because it has a smaller memory and compute footprint than larger instruction models.

The project can therefore demonstrate:

- local inference
- PEFT/LoRA
- structured generation
- fallback behavior

without making the entire application dependent on a hosted proprietary model.

## 6. Model limitations

Generated text is not treated as the authoritative source for:

- transaction totals
- portfolio valuation
- invoice totals
- persisted user state

Those values are calculated from application data and tools.

This distinction is important when explaining the system during a viva: the LLM is an intelligence/generation component, not the database.
