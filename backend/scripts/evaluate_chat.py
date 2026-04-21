"""Evaluate routing accuracy + format compliance on a held-out prompt set."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import httpx

TAG_RE = re.compile(r"^\[AGENT:\s*(BUDGET|INVESTMENT|INVOICE)\]\s*$", re.I)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSONL at line {line_no}: {e}") from e
            rows.append(obj)
    return rows


def _is_format_compliant(reply: str) -> bool:
    parts = [p for p in reply.replace("\r", "").split("\n") if p.strip()]
    if len(parts) < 3:
        return False
    if not TAG_RE.match(parts[0].strip()):
        return False
    json_line = parts[-1].strip()
    if not (json_line.startswith("{") and json_line.endswith("}")):
        return False
    try:
        payload = json.loads(json_line)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    req = {"intent", "steps", "tools_needed", "notes"}
    return req.issubset(payload.keys()) and isinstance(payload.get("steps"), list)


def evaluate(base_url: str, token: str, dataset: Path) -> None:
    rows = _load_jsonl(dataset)
    if not rows:
        raise ValueError("Dataset is empty.")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    total = 0
    route_correct = 0
    format_ok = 0
    failures: list[str] = []

    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0) as client:
        for idx, row in enumerate(rows, start=1):
            message = str(row.get("message", "")).strip()
            expected = str(row.get("expected_agent", "")).strip()
            if not message or not expected:
                failures.append(f"{idx}: missing message/expected_agent")
                continue

            res = client.post("/api/chat/message", headers=headers, json={"message": message})
            if res.status_code != 200:
                failures.append(f"{idx}: HTTP {res.status_code} {res.text[:120]}")
                continue

            data = res.json()
            agent = str(data.get("agent", ""))
            reply = str(data.get("reply", ""))
            total += 1
            if agent == expected:
                route_correct += 1
            if _is_format_compliant(reply):
                format_ok += 1

    if total == 0:
        raise RuntimeError("No rows were successfully evaluated.")

    route_acc = 100.0 * route_correct / total
    format_rate = 100.0 * format_ok / total

    print("Evaluation complete")
    print(f"- dataset rows considered: {total}")
    print(f"- routing accuracy: {route_correct}/{total} ({route_acc:.2f}%)")
    print(f"- format compliance: {format_ok}/{total} ({format_rate:.2f}%)")
    if failures:
        print(f"- skipped/failed rows: {len(failures)}")
        for f in failures[:15]:
            print(f"  - {f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate FinMate chat routing and output format.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True, help="Bearer token from /api/auth/login or /api/auth/register")
    parser.add_argument(
        "--dataset",
        default="../training/data/eval_prompts_heldout.jsonl",
        help="JSONL with: message, expected_agent",
    )
    args = parser.parse_args()
    evaluate(args.base_url, args.token, Path(args.dataset))


if __name__ == "__main__":
    main()
