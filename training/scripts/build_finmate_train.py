#!/usr/bin/env python3
"""
Build finmate_train.jsonl from CSVs (balanced-ish row limits).

  cd training
  python scripts/build_finmate_train.py

Then:
  python scripts/analyze_finmate_dataset.py data/finmate_train.jsonl
  python scripts/sample_finmate_small.py --input data/finmate_train.jsonl --out data/finmate_train_small.jsonl --total 4500
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
CONV = ROOT / "scripts" / "csv_to_sft.py"

# (csv, format, out, row_limit) — limits reduce Indian dominance; invoice duplicates tracker rows with invoice prompts
PARTS: list[tuple[str, str, str, int]] = [
    ("data/personal_finance_tracker_dataset.csv", "tracker", "data/_part_tracker.jsonl", 0),
    ("data/Indian Personal Finance and Spending Habits.csv", "indian", "data/_part_indian.jsonl", 3000),
    ("data/personal_finance_tracker_dataset.csv", "invoice", "data/_part_invoice.jsonl", 2000),
    ("data/investment_survey_dataset.csv", "investment_survey", "data/_part_survey.jsonl", 0),
    ("data/finance_economics_dataset.csv", "macro", "data/_part_macro.jsonl", 0),
]


def run_one(csv_rel: str, fmt: str, out_rel: str, limit: int) -> None:
    csv_path = ROOT / csv_rel
    out_path = ROOT / out_rel
    if not csv_path.is_file():
        print(f"Skip missing: {csv_path}", file=sys.stderr)
        return
    cmd = [PY, str(CONV), "--csv", str(csv_path), "--out", str(out_path), "--format", fmt]
    if limit > 0:
        cmd.extend(["--limit", str(limit)])
    print(" ", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def main() -> None:
    for csv_rel, fmt, out_rel, lim in PARTS:
        run_one(csv_rel, fmt, out_rel, lim)

    out_final = ROOT / "data" / "finmate_train.jsonl"
    parts_existing = [ROOT / p for _, _, p, _ in PARTS if (ROOT / p).is_file()]
    if not parts_existing:
        print("No part files generated.", file=sys.stderr)
        sys.exit(1)

    with out_final.open("wb") as merged:
        for p in parts_existing:
            with p.open("rb") as f:
                shutil.copyfileobj(f, merged)

    seed = ROOT / "data" / "example_sft.jsonl"
    if seed.is_file():
        with out_final.open("ab") as merged:
            shutil.copyfileobj(seed.open("rb"), merged)

    n_lines = sum(1 for _ in out_final.open(encoding="utf-8"))

    stats = ROOT / "data" / "finmate_train_stats.txt"
    msg = (
        f"finmate_train.jsonl\n"
        f"lines: {n_lines}\n"
        f"sources: {[p.name for p in parts_existing]}\n"
        f"+ example_sft.jsonl (3 lines)\n"
    )
    stats.write_text(msg, encoding="utf-8")
    print(msg)
    print(f"Wrote {out_final}")
    print("Next: python scripts/analyze_finmate_dataset.py data/finmate_train.jsonl")
    print("      python scripts/sample_finmate_small.py --input data/finmate_train.jsonl --out data/finmate_train_small.jsonl --total 4500")


if __name__ == "__main__":
    main()
