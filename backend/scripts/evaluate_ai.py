"""Evaluate FinMate's end-to-end AI contract on a held-out prompt set.

This evaluator measures observable API behavior only. It does not claim that
routing/format scores represent general model quality.
"""

from __future__ import annotations

import argparse
import json
import re
import time
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

    with httpx.Client(base_url=args.base_url.rstrip("/"), headers=headers, timeout=None) as client:
        for idx, row in enumerate(rows, 1):
            message = str(row.get("message", "")).strip()
            expected_agent = str(row.get("expected_agent", "")).strip()
            expected_source = str(row.get("expected_source", "")).strip()
            expected_agents = [str(x) for x in row.get("expected_agents", [])]
            needs_invoice_artifacts = bool(row.get("needs_invoice_artifacts", False))

            metrics["total"] += 1
            preview = " ".join(message.split())
            if len(preview) > 72:
                preview = preview[:69] + "..."
            print(f"[{idx}/{len(rows)}] {preview}", flush=True)

            started = time.perf_counter()
            try:
                response = client.post(
                    "/api/chat/message",
                    json={"message": message},
                )
                elapsed = time.perf_counter() - started
                if response.status_code != 200:
                    failures.append(f"{idx}: HTTP {response.status_code} after {elapsed:.1f}s")
                    print(f"    FAIL HTTP {response.status_code} ({elapsed:.1f}s)", flush=True)
                    continue
                metrics["http_ok"] += 1
                data = response.json()
                print(f"    OK ({elapsed:.1f}s)", flush=True)
            except Exception as exc:
                elapsed = time.perf_counter() - started
                failures.append(f"{idx}: request error after {elapsed:.1f}s: {exc}")
                print(f"    FAIL after {elapsed:.1f}s: {exc}", flush=True)
                continue

            agent = str(data.get("agent", ""))
            reply = str(data.get("reply", ""))
            meta = data.get("metadata") or {}

            case_issues: list[str] = []
            if expected_agent and agent != expected_agent:
                case_issues.append(f"agent expected={expected_agent}, got={agent}")
            elif expected_agent:
                metrics["route_correct"] += 1

            if format_ok(reply):
                metrics["format_ok"] += 1
            else:
                case_issues.append("format contract failed")

            if "confidence" in meta and "confidence_level" in meta:
                metrics["confidence_present"] += 1
            else:
                case_issues.append("confidence metadata missing")

            if str(meta.get("rag_chunks_used", "0")) != "0":
                metrics["rag_observed"] += 1

            if expected_source == "agentic":
                metrics["agentic_cases"] += 1
                executed = [x for x in str(meta.get("agents_executed", "")).split(",") if x]
                if executed == expected_agents:
                    metrics["agentic_correct"] += 1
                else:
                    case_issues.append(f"agents expected={expected_agents}, got={executed}")

            if needs_invoice_artifacts:
                metrics["invoice_artifact_cases"] += 1
                required = ("invoice_ref", "invoice_payload", "invoice_actions")
                if all(str(meta.get(k, "")).strip() for k in required):
                    metrics["invoice_artifacts_present"] += 1
                else:
                    case_issues.append("invoice artifacts missing; metadata keys=" + ",".join(sorted(str(k) for k in meta.keys())))

            if case_issues:
                failures.append(f"{idx}: " + "; ".join(case_issues))
                print("    ISSUES: " + "; ".join(case_issues), flush=True)

    evaluated = metrics["http_ok"]

    def pct(value: int, denominator: int = evaluated) -> str:
        return f"{100.0 * value / denominator:.2f}%" if denominator else "N/A"

    print()
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
