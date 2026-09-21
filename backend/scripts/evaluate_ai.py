"""Evaluate FinMate's end-to-end AI contract on a held-out prompt set.

This evaluator measures observable API behavior only. It does not claim that
routing/format scores represent general model quality.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import httpx

TAG_RE = re.compile(r"^\[AGENT:\s*(BUDGET|INVESTMENT|INVOICE)\]\s*$", re.I)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid JSONL line {line_no}: {e}") from e
    return rows


def format_ok(reply: str) -> bool:
    parts = [x.strip() for x in reply.replace("\r", "").split("\n") if x.strip()]
    if len(parts) < 3 or not TAG_RE.match(parts[0]):
        return False
    try:
        payload = json.loads(parts[-1])
    except json.JSONDecodeError:
        return False
    return (
        isinstance(payload, dict)
        and {"intent", "steps", "tools_needed", "notes"} <= payload.keys()
        and isinstance(payload["steps"], list)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument(
        "--dataset",
        default="../training/data/final_ai_eval.jsonl",
    )
    args = parser.parse_args()

    rows = load_jsonl(Path(args.dataset))
    if not rows:
        raise ValueError("Evaluation dataset is empty.")

    headers = {"Authorization": f"Bearer {args.token}", "Content-Type": "application/json"}
    metrics = {
        "total": 0,
        "http_ok": 0,
        "route_correct": 0,
        "format_ok": 0,
        "confidence_present": 0,
        "rag_observed": 0,
        "agentic_cases": 0,
        "agentic_correct": 0,
        "invoice_artifact_cases": 0,
        "invoice_artifacts_present": 0,
    }
    failures: list[str] = []

    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=90) as client:
        for idx, row in enumerate(rows, 1):
            message = str(row.get("message", "")).strip()
            expected_agent = str(row.get("expected_agent", "")).strip()
            expected_source = str(row.get("expected_source", "")).strip()
            expected_agents = [str(x) for x in row.get("expected_agents", [])]
            needs_invoice_artifacts = bool(row.get("needs_invoice_artifacts", False))

            metrics["total"] += 1
            try:
                response = client.post("/api/chat/message", json={"message": message})
                if response.status_code != 200:
                    failures.append(f"{idx}: HTTP {response.status_code}")
                    continue
                metrics["http_ok"] += 1
                data = response.json()
            except Exception as exc:
                failures.append(f"{idx}: request error: {exc}")
                continue

            agent = str(data.get("agent", ""))
            reply = str(data.get("reply", ""))
            meta = data.get("metadata") or {}

            if expected_agent and agent == expected_agent:
                metrics["route_correct"] += 1
            if format_ok(reply):
                metrics["format_ok"] += 1
            if "confidence" in meta and "confidence_level" in meta:
                metrics["confidence_present"] += 1
            if str(meta.get("rag_chunks_used", "0")) != "0":
                metrics["rag_observed"] += 1

            if expected_source == "agentic":
                metrics["agentic_cases"] += 1
                executed = [x for x in str(meta.get("agents_executed", "")).split(",") if x]
                if executed == expected_agents:
                    metrics["agentic_correct"] += 1
                else:
                    failures.append(
                        f"{idx}: expected agents {expected_agents}, got {executed}"
                    )

            if needs_invoice_artifacts:
                metrics["invoice_artifact_cases"] += 1
                required = ("invoice_ref", "invoice_payload", "invoice_actions")
                if all(str(meta.get(k, "")).strip() for k in required):
                    metrics["invoice_artifacts_present"] += 1
                else:
                    failures.append(f"{idx}: missing invoice artifacts")

    evaluated = metrics["http_ok"]

    def pct(value: int, denominator: int = evaluated) -> str:
        return f"{100.0 * value / denominator:.2f}%" if denominator else "N/A"

    print("Final AI Evaluation")
    print("===================")
    print(f"Dataset cases:             {metrics['total']}")
    print(f"HTTP-successful cases:     {metrics['http_ok']}")
    print(f"Routing accuracy:          {metrics['route_correct']}/{evaluated} ({pct(metrics['route_correct'])})")
    print(f"Format compliance:         {metrics['format_ok']}/{evaluated} ({pct(metrics['format_ok'])})")
    print(f"Confidence metadata:       {metrics['confidence_present']}/{evaluated} ({pct(metrics['confidence_present'])})")
    print(f"Cases with RAG observed:   {metrics['rag_observed']}/{evaluated} ({pct(metrics['rag_observed'])})")

    if metrics["agentic_cases"]:
        print(
            f"Agentic plan execution:    {metrics['agentic_correct']}/"
            f"{metrics['agentic_cases']} ({pct(metrics['agentic_correct'], metrics['agentic_cases'])})"
        )
    if metrics["invoice_artifact_cases"]:
        print(
            f"Invoice artifacts:         {metrics['invoice_artifacts_present']}/"
            f"{metrics['invoice_artifact_cases']} "
            f"({pct(metrics['invoice_artifacts_present'], metrics['invoice_artifact_cases'])})"
        )

    if failures:
        print(f"Failures/issues:            {len(failures)}")
        for failure in failures[:20]:
            print(f"  - {failure}")
    else:
        print("Failures/issues:            0")


if __name__ == "__main__":
    main()
