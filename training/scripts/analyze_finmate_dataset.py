#!/usr/bin/env python3
"""Report agent tag counts, JSON validity, and simple repetition hints on a JSONL file."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


AGENT_RE = re.compile(r"^\[AGENT:\s*(BUDGET|INVESTMENT|INVOICE)\s*\]", re.M)


def first_agent(assistant: str) -> str:
    m = AGENT_RE.search(assistant.strip())
    if m:
        return m.group(1)
    return "MISSING"


def last_json_line(text: str) -> str | None:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    if last.startswith("{") and last.endswith("}"):
        return last
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", type=Path, help="Path to finmate_train.jsonl")
    ap.add_argument("--sample", type=int, default=5, help="Print N random assistant prefixes")
    args = ap.parse_args()

    if not args.jsonl.is_file():
        print(f"Not found: {args.jsonl}", file=sys.stderr)
        sys.exit(1)

    agents: Counter[str] = Counter()
    bad_tail = 0
    missing_agent = 0
    empty_asst = 0
    bad_line = 0
    opens: list[str] = []

    with args.jsonl.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                bad_line += 1
                continue
            msgs = obj.get("messages") or []
            asst = ""
            for m in msgs:
                if m.get("role") == "assistant":
                    asst = m.get("content") or ""
            if not asst.strip():
                empty_asst += 1
                continue
            ag = first_agent(asst)
            if ag == "MISSING":
                missing_agent += 1
            agents[ag] += 1
            lj = last_json_line(asst)
            if not lj:
                bad_tail += 1
            else:
                try:
                    json.loads(lj)
                except json.JSONDecodeError:
                    bad_tail += 1
            if len(opens) < args.sample:
                opens.append(asst[:280].replace("\n", " "))

    total = sum(agents.values()) or 1
    print("=== FinMate dataset report ===")
    print(f"file: {args.jsonl}")
    print(f"assistant messages counted: {total}")
    print("agent tags (from [AGENT: ...]):")
    for k in ("BUDGET", "INVESTMENT", "INVOICE", "MISSING"):
        c = agents.get(k, 0)
        print(f"  {k}: {c} ({100 * c / total:.1f}%)")
    print(f"bad JSONL lines: {bad_line}")
    print(f"assistant missing final JSON line: {bad_tail}")
    print(f"empty assistant: {empty_asst}")
    print("\nSample assistant openings:")
    for s in opens:
        print(f"  - {s[:240]}...")

    print("\nTip: target mix ~ BUDGET 40–50%, INVESTMENT 30–40%, INVOICE 10–20%.")


if __name__ == "__main__":
    main()
