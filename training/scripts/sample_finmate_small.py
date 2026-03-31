#!/usr/bin/env python3
"""
Stratified sample from finmate_train.jsonl → finmate_train_small.jsonl (default 4500 lines).

Target mix (approx): BUDGET 45%, INVESTMENT 35%, INVOICE 20%.

  cd training
  python scripts/sample_finmate_small.py --in data/finmate_train.jsonl --out data/finmate_train_small.jsonl --total 4500
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path


AGENT_RE = re.compile(r"^\[AGENT:\s*(BUDGET|INVESTMENT|INVOICE)\s*\]", re.M)


def first_agent(assistant: str) -> str:
    m = AGENT_RE.search(assistant.strip())
    if m:
        return m.group(1)
    return "MISSING"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=Path("data/finmate_train.jsonl"), dest="inp")
    ap.add_argument("--out", type=Path, default=Path("data/finmate_train_small.jsonl"))
    ap.add_argument("--total", type=int, default=4500)
    ap.add_argument("--p-budget", type=float, default=0.45)
    ap.add_argument("--p-invest", type=float, default=0.35)
    ap.add_argument("--p-invoice", type=float, default=0.20)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    if not args.inp.is_file():
        print(f"Not found: {args.inp}", file=sys.stderr)
        sys.exit(1)

    buckets: dict[str, list[str]] = {"BUDGET": [], "INVESTMENT": [], "INVOICE": [], "MISSING": []}
    with args.inp.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            asst = ""
            for m in obj.get("messages") or []:
                if m.get("role") == "assistant":
                    asst = m.get("content") or ""
            ag = first_agent(asst)
            key = ag if ag in buckets else "MISSING"
            buckets[key].append(line)

    tb = int(args.total * args.p_budget)
    ti = int(args.total * args.p_invest)
    tv = int(args.total * args.p_invoice)

    def take(pool: list[str], n: int) -> list[str]:
        p = pool[:]
        if len(p) <= n:
            return p
        random.shuffle(p)
        return p[:n]

    out_lines: list[str] = []
    out_lines.extend(take(buckets["BUDGET"], tb))
    out_lines.extend(take(buckets["INVESTMENT"], ti))
    out_lines.extend(take(buckets["INVOICE"], tv))

    # Fill up to total from remainder if short
    if len(out_lines) < args.total:
        have = set(out_lines)
        rest = [l for pool in buckets.values() for l in pool if l not in have]
        random.shuffle(rest)
        for line in rest:
            if len(out_lines) >= args.total:
                break
            out_lines.append(line)

    random.shuffle(out_lines)
    out_lines = out_lines[: args.total]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as w:
        for ln in out_lines:
            w.write(ln + "\n")

    # Count
    c = {"BUDGET": 0, "INVESTMENT": 0, "INVOICE": 0, "MISSING": 0}
    for ln in out_lines:
        obj = json.loads(ln)
        asst = ""
        for m in obj.get("messages") or []:
            if m.get("role") == "assistant":
                asst = m.get("content") or ""
        ag = first_agent(asst)
        if ag in c:
            c[ag] += 1
        else:
            c["MISSING"] += 1

    print(f"Wrote {len(out_lines)} lines to {args.out}")
    print("mix:", {k: f"{v} ({100*v/len(out_lines):.1f}%)" for k, v in c.items()})


if __name__ == "__main__":
    main()
