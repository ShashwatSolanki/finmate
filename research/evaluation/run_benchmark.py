"""Run a reproducible FinMate API or direct-Qwen benchmark on synthetic data only."""
from __future__ import annotations
import argparse, hashlib, json, os, platform, re, statistics, subprocess, sys, time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
import httpx

ROOT=Path(__file__).resolve().parents[2]
BACKEND=ROOT/"backend"
if str(BACKEND) not in sys.path: sys.path.insert(0,str(BACKEND))
NUM_RE=re.compile(r"(?<![A-Za-z])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")

def load_jsonl(path):
    rows=[]
    for n,line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(),1):
        if line.strip():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError as e: raise ValueError(f"Invalid JSONL line {n}: {e}") from e
    return rows

def numeric_values(text):
    out=[]
    for m in NUM_RE.finditer(text):
        try: out.append(float(m.group(0).rstrip("%").replace(",","")))
        except ValueError: pass
    return out

def contains_number(text,target,tol):
    return any(abs(v-target)<=tol for v in numeric_values(text))

def invoice_payload_correct(meta,gold,tol=0.01):
    raw=meta.get("invoice_payload")
    if not raw: return False
    try:
        payload=json.loads(raw) if isinstance(raw,str) else raw
        if not isinstance(payload,dict) or str(payload.get("currency","")).upper()!=str(gold.get("currency","")).upper(): return False
        amounts=sorted(round(float(x.get("amount",0)),2) for x in payload.get("line_items",[]))
        expected=sorted(round(float(x),2) for x in gold.get("line_amounts",[]))
        subtotal=payload.get("subtotal")
        if subtotal is None: subtotal=sum(amounts)
        return amounts==expected and abs(float(subtotal)-float(gold.get("subtotal",0)))<=tol
    except (TypeError,ValueError,json.JSONDecodeError): return False

def parse_agent(reply,api_agent=""):
    if api_agent: return api_agent
    m=re.search(r"\[AGENT:\s*(BUDGET|INVESTMENT|INVOICE)\s*\]",reply,re.I)
    return {"BUDGET":"budget_planner","INVESTMENT":"investment_analyser","INVOICE":"invoice_generator"}.get(m.group(1).upper(),"") if m else ""

def reset_and_seed(user_id,case):
    from uuid import UUID
    from app.db.models import Budget,ChatSession,InvestmentHolding,MemoryChunk,Transaction
    from app.db.session import SessionLocal
    from app.rag.memory_store import rank_memory
    uid=UUID(user_id); db=SessionLocal()
    try:
        for session in db.query(ChatSession).filter(ChatSession.user_id==uid).all(): db.delete(session)
        db.query(MemoryChunk).filter(MemoryChunk.user_id==uid).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.user_id==uid).delete(synchronize_session=False)
        db.query(Budget).filter(Budget.user_id==uid).delete(synchronize_session=False)
        db.query(InvestmentHolding).filter(InvestmentHolding.user_id==uid).delete(synchronize_session=False)
        for content in case.get("memory_fixture",[]): db.add(MemoryChunk(user_id=uid,content=content,source="research_eval"))
        today=date.today()
        for item in case.get("transactions_fixture",[]):
            db.add(Transaction(user_id=uid,amount=Decimal(str(item["amount"])),currency=item.get("currency","INR"),
              category=item.get("category"),description=item.get("description","synthetic evaluation transaction"),
              occurred_on=today-timedelta(days=int(item.get("days_ago",0)))))
        db.commit()
        if not case.get("relevant_memory"): return None,None
        ranked=rank_memory(db,uid,case["message"],limit=5,min_similarity=0.22)
        ranks=[i+1 for i,(txt,_score) in enumerate(ranked) if txt==case["relevant_memory"]]
        return (1.0 if ranks else 0.0),(1.0/ranks[0] if ranks else 0.0)
    finally: db.close()

def run():
    p=argparse.ArgumentParser()
    p.add_argument("--condition",choices=["full","no_rag","no_agentic","no_llm","llm_only"],required=True)
    p.add_argument("--base-url",default="http://127.0.0.1:8000")
    p.add_argument("--token",default="")
    p.add_argument("--user-id",default="")
    p.add_argument("--reset-test-user-state",action="store_true")
    p.add_argument("--dataset",type=Path,default=Path(__file__).with_name("benchmark.jsonl"))
    p.add_argument("--output",type=Path,default=None)
    p.add_argument("--limit",type=int,default=0,help="Take the first N cases for debugging only; not stratified.")
    p.add_argument("--pilot",action="store_true",help="Select a deterministic stratified 21-case smoke pilot across numerical, memory/abstention, single-agent and multi-agent tasks.")
    p.add_argument("--commit",default="")
    p.add_argument("--environment-notes",default="")
    args=p.parse_args()
    if not args.dataset.exists(): raise SystemExit(f"Dataset not found: {args.dataset}. Run generate_benchmark.py first.")
    cases=load_jsonl(args.dataset)
    if args.pilot:
        def take(group, subgroup, indices):
            rows=[x for x in cases if x.get("group")==group and (subgroup is None or x.get("subgroup")==subgroup)]
            return [rows[i] for i in indices if i < len(rows)]
        selected=[]
        selected += take("numerical","transaction_aggregation",[0,10])
        selected += take("numerical","invoice_subtotal",[0,5])
        selected += take("numerical","balance_arithmetic",[0,5])
        selected += take("memory","memory_income",[0])
        selected += take("memory","memory_risk",[2])
        selected += take("memory","memory_goal",[4])
        selected += take("memory","memory_absent",[0,4])
        selected += take("routing","single_specialist",[0,5,10,15])
        selected += take("routing","budget_investment",[0,4])
        selected += take("routing","budget_invoice",[1,5])
        selected += take("routing","three_domain",[2,4])
        cases=selected
    elif args.limit: cases=cases[:args.limit]
    if not cases: raise SystemExit("Dataset is empty.")
    api_mode=args.condition!="llm_only"
    if api_mode and (not args.token or not args.user_id or not args.reset_test_user_state):
        raise SystemExit("API conditions require --token, --user-id and --reset-test-user-state. Use a disposable synthetic test account/database only.")
    api=httpx.Client(base_url=args.base_url.rstrip("/"),headers={"Authorization":f"Bearer {args.token}","Content-Type":"application/json"},timeout=180.0) if api_mode else None
    rows=[]
    try:
        for i,case in enumerate(cases,1):
            hit5=mrr=None
            if api_mode: hit5,mrr=reset_and_seed(args.user_id,case)
            start=time.perf_counter(); status=None; data={}; error=""
            try:
                if not api_mode:
                    from app.ml.finmate import generate
                    reply=generate(case["message"])
                    data={"reply":reply,"agent":parse_agent(reply)}
                else:
                    response=api.post("/api/chat/message",json={"message":case["message"]}); status=response.status_code
                    if status==200: data=response.json()
                    else: error=f"HTTP {status}: {response.text[:500]}"
            except Exception as exc: error=f"{type(exc).__name__}: {exc}"
            latency=time.perf_counter()-start
            reply=str(data.get("reply","")); meta=data.get("metadata") or {}
            agent=parse_agent(reply,str(data.get("agent","")))
            blob=reply+"\n"+json.dumps(meta,ensure_ascii=False,default=str)
            gold_nums=case.get("gold_numeric_values",[]); tol=float(case.get("tolerance",0.01))
            numeric_ok=all(contains_number(blob,float(v),tol) for v in gold_nums) if gold_nums else None
            gold_mem=str(case.get("gold_memory_answer") or "").strip().lower()
            memory_ok=(gold_mem in blob.lower()) if gold_mem else None
            route_ok=(agent==case["expected_agent"]) if case.get("expected_agent") else None
            executed=[x for x in str(meta.get("agents_executed","")).split(",") if x]
            agentic_ok=(executed==case.get("expected_agents",[])) if case.get("expected_source")=="agentic" else None
            artifact_present=all(str(meta.get(k,"")).strip() for k in ("invoice_ref","invoice_payload","invoice_actions"))
            artifacts_ok=artifact_present if case.get("requires_invoice_artifacts") else None
            invoice_ok=invoice_payload_correct(meta,case["gold_invoice"],tol) if case.get("gold_invoice") else None
            abstention_ok=None
            if case.get("expected_abstention"):
                low=reply.lower()
                cues=("not stored","not in your profile","don't have","do not have","not available","can't determine","cannot determine","don't see","do not see","not found","not provided","unable to tell")
                abstention_ok=any(cue in low for cue in cues)
            checks=[]
            for val in (numeric_ok,memory_ok,route_ok,agentic_ok,artifacts_ok,invoice_ok,abstention_ok):
                if val is not None: checks.append(val)
            http_ok=(status==200) if api_mode else not bool(error)
            rows.append({"case_id":case["case_id"],"group":case["group"],"subgroup":case.get("subgroup",""),
              "condition":args.condition,"http_status":status,"http_ok":http_ok,"error":error,"latency_seconds":round(latency,4),
              "agent":agent,"expected_agent":case.get("expected_agent"),"route_correct":route_ok,"numeric_correct":numeric_ok,
              "memory_correct":memory_ok,"abstention_correct":abstention_ok,"agentic_correct":agentic_ok,
              "agents_executed":executed,"expected_agents":case.get("expected_agents"),"invoice_artifacts_present":artifacts_ok,
              "invoice_payload_correct":invoice_ok,"rag_chunks_used":meta.get("rag_chunks_used"),
              "retrieval_hit_at_5":hit5,"retrieval_reciprocal_rank":mrr,
              "task_completed":bool(http_ok and checks and all(checks)),"gold_numeric_values":gold_nums,
              "gold_memory_answer":case.get("gold_memory_answer"),"reply":reply,"metadata":meta,"case_spec":case})
            print(f"[{i}/{len(cases)}] {case['case_id']} {'OK' if http_ok else 'FAIL'} route={agent or '-'} latency={latency:.2f}s",flush=True)
    finally:
        if api is not None: api.close()
    output=args.output or Path(f"results_{args.condition}.jsonl")
    output.parent.mkdir(parents=True,exist_ok=True)
    raw=("\n".join(json.dumps(x,ensure_ascii=False,default=str) for x in rows)+"\n").encode()
    output.write_bytes(raw)
    commit=args.commit.strip()
    if not commit:
        try: commit=subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
        except Exception: commit="not-detected; set --commit explicitly"
    manifest={"timestamp_utc":datetime.now(timezone.utc).isoformat(),"condition":args.condition,"cases_requested":len(cases),
      "dataset_path":str(args.dataset.resolve()),"dataset_sha256":hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
      "results_path":str(output.resolve()),"results_sha256":hashlib.sha256(raw).hexdigest(),"repository_commit":commit,
      "python_version":sys.version,"platform":platform.platform(),"api_base_url":args.base_url if api_mode else None,
      "runner_environment_flags":{k:os.getenv(k) for k in ["FINMATE_USE_RAG","FINMATE_AGENTIC_MODE","FINMATE_USE_LLM","FINMATE_USE_EMBEDDINGS","FINMATE_LORA_PATH"]},
      "note":"For API conditions, runner flags are not necessarily server flags. Record server settings in environment_notes. Secrets are deliberately excluded.",
      "environment_notes":args.environment_notes}
    manifest_path=output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(f"Saved raw results: {output}\nSaved manifest: {manifest_path}\nHTTP success: {sum(r['http_ok'] for r in rows)}/{len(rows)}\nTask completion: {sum(r['task_completed'] for r in rows)}/{len(rows)}")
    return 0

if __name__=="__main__": raise SystemExit(run())
