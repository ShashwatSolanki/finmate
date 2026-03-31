"""FinMate QLoRA model loader and inference (Qwen2.5-1.5B-Instruct + PEFT adapter)."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

VALID_KEYS = {"intent", "steps", "tools_needed", "notes"}

STEPS_FALLBACK_BUDGET = ["Review spending", "Set weekly cap", "Automate savings"]
STEPS_FALLBACK_INVEST = [
    "Clarify time horizon and risk tolerance",
    "Favor diversified low-cost index exposure",
    "Keep emergency cash separate from long-term investments",
]
STEPS_FALLBACK_INVOICE = [
    "Parse line items and amounts",
    "Compute totals and taxes",
    "Export or download the PDF",
]

# If the model emits these on the investment specialist path, they are wrong — prefer yfinance_lookup.
_BUDGET_TOOL_IDS = frozenset({"list_transactions", "set_budget", "categorize_spending"})

CRISIS_KEYWORDS = [
    "100%",
    "all my money",
    "nothing left",
    "broke",
    "can't pay rent",
    "evicted",
    "no income",
    "lost my job",
]

SYSTEM = (
    "You are FinMate, a helpful financial assistant. Always reply in English only.\n"
    "Rules for every reply:\n"
    "1) Start with exactly one line: [AGENT: BUDGET], [AGENT: INVESTMENT], or [AGENT: INVOICE].\n"
    "2) Then write 2-4 sentences in plain English: empathetic, specific, with reasoning (not only raw percentages).\n"
    "3) End with a single line of valid JSON (no code fences) with keys: intent, steps (array of short strings), "
    "tools_needed (JSON array of short machine-readable ids like list_transactions or yfinance_lookup — never English prose), "
    "notes (string, optional)."
)

# Appended to SYSTEM when the investment specialist calls generate (reduces JSON-only / malformed tool lists).
SYSTEM_EXTRA_INVESTMENT = (
    "\nThis turn is the INVESTMENT flow. Line 1 must be exactly [AGENT: INVESTMENT]. "
    "Do not reply with JSON only — you must include the tag line and plain-English sentences before the JSON line. "
    "tools_needed must be a JSON array of short tokens such as [\"yfinance_lookup\"], not sentences."
)


def _resolve_lora_root() -> Path:
    raw = (settings.finmate_lora_path or "").strip()
    if not raw:
        return Path(__file__).resolve().parent / "finmate-lora"
    p = Path(raw)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def _find_adapter_dir(root: Path) -> Path:
    """Prefer root; else newest checkpoint-* that contains adapter weights."""
    candidates = [root] + sorted(root.glob("checkpoint-*"), key=lambda x: x.name, reverse=True)
    for cand in candidates:
        if not cand.is_dir():
            continue
        if (cand / "adapter_model.safetensors").is_file() or (cand / "adapter_model.bin").is_file():
            return cand
    raise FileNotFoundError(
        f"No LoRA weights (adapter_model.safetensors or adapter_model.bin) under {root}. "
        "Copy your trained adapter from Colab into this folder or set FINMATE_LORA_PATH."
    )


def _read_base_model_name(adapter_dir: Path) -> str:
    cfg = adapter_dir / "adapter_config.json"
    if cfg.is_file():
        try:
            meta = json.loads(cfg.read_text(encoding="utf-8"))
            name = meta.get("base_model_name_or_path")
            if isinstance(name, str) and name:
                return name
        except (json.JSONDecodeError, OSError):
            pass
    return "Qwen/Qwen2.5-1.5B-Instruct"


@lru_cache(maxsize=1)
def _load_model():
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    root = _resolve_lora_root()
    adapter_dir = _find_adapter_dir(root)
    base_name = _read_base_model_name(adapter_dir)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    logger.info("Loading FinMate LoRA: base=%s adapter=%s device=%s", base_name, adapter_dir, device)

    base = AutoModelForCausalLM.from_pretrained(
        base_name,
        device_map="auto" if device == "cuda" else None,
        torch_dtype=dtype,
    )
    if device == "cpu":
        base = base.to(device)

    tokenizer = AutoTokenizer.from_pretrained(base_name)
    tokenizer.pad_token = tokenizer.eos_token

    model = PeftModel.from_pretrained(base, str(adapter_dir))
    model.eval()
    model.config.use_cache = True

    return model, tokenizer, device


def _normalize_tools_needed(val: object, *, fallback: list[str]) -> list[str]:
    if isinstance(val, list):
        out: list[str] = []
        for x in val:
            if not isinstance(x, str):
                continue
            s = x.strip()
            if not s or len(s) > 48:
                continue
            low = s.lower()
            if any(c.isspace() for c in s) or ":" in s:
                continue
            if "needed" in low and "tool" in low:
                continue
            token = re.sub(r"[^a-zA-Z0-9_\-]", "", s).lower() or "tool"
            if token not in out:
                out.append(token)
        return out[:8] if out else list(fallback)
    if isinstance(val, str) and val.strip():
        return list(fallback)
    return list(fallback)


def _normalize_steps(val: object, *, fallback: list[str]) -> list[str]:
    if not isinstance(val, list):
        return list(fallback)
    out: list[str] = []
    for x in val:
        if not isinstance(x, str):
            continue
        s = " ".join(str(x).split()).strip()
        if 2 <= len(s) <= 220:
            out.append(s)
    return out[:12] if out else list(fallback)


def _last_brace_object_span(s: str) -> tuple[int, int] | None:
    start = s.rfind("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
    return None


def _span_eat_wrapping_parens(s: str, start: int, end: int) -> tuple[int, int]:
    """Include a leading '(' before '{' and matching ')' after '}' so the whole wrapper is replaced."""
    a, b = start, end
    if a > 0 and s[a - 1] == "(":
        j = b
        while j < len(s) and s[j] in " \t\n\r":
            j += 1
        if j < len(s) and s[j] == ")":
            return a - 1, j + 1
    return start, end


def _extend_span_trailing_paren_after_brace(s: str, end: int) -> int:
    """Drop a stray `)` the model adds after the closing `}` (e.g. `{...})`)."""
    j = end
    while j < len(s) and s[j] in " \t\n\r":
        j += 1
    if j < len(s) and s[j] == ")":
        return j + 1
    return end


def _unwrap_outer_parens(blob: str) -> str:
    t = blob.strip()
    while t.startswith("(") and t.endswith(")"):
        inner = t[1:-1].strip()
        if not inner.startswith("{"):
            break
        t = inner
    return t


def _normalize_json_typos(s: str) -> str:
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u00ab", '"').replace("\u00bb", '"')
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return s


def _regex_fallback_finmate_dict(raw: str) -> dict:
    """When json.loads fails (smart quotes, broken arrays), pull what we can."""
    out: dict = {
        "intent": "investment_info",
        "steps": list(STEPS_FALLBACK_INVEST),
        "tools_needed": ["yfinance_lookup"],
        "notes": "",
    }
    m = re.search(r'"intent"\s*:\s*"([^"]*)"', raw)
    if m:
        out["intent"] = m.group(1).strip().lower().replace(" ", "_")[:64] or out["intent"]
    m = re.search(r'"notes"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if not m:
        m = re.search(r'"notes"\s*:\s*"([^"]*)"', raw)
    if m:
        out["notes"] = m.group(1).replace("\\n", " ").strip()[:500]
    return out


def _parse_finmate_dict(blob: str) -> dict | None:
    raw_inner = _unwrap_outer_parens(blob)
    candidate = _normalize_json_typos(raw_inner)
    try:
        d = json.loads(candidate)
        if isinstance(d, dict):
            return d
    except json.JSONDecodeError:
        logger.debug("finmate json parse failed; regex fallback. snippet=%r", candidate[:160])
    fb = _regex_fallback_finmate_dict(raw_inner)
    return fb


def _extract_json_line(response: str) -> str | None:
    span = _last_brace_object_span(response)
    if not span:
        return None
    blob = response[span[0] : span[1]]
    parsed = _parse_finmate_dict(blob)
    if not parsed:
        return None
    payload = {k: v for k, v in parsed.items() if k in VALID_KEYS}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _postprocess(response: str, *, tools_fallback: list[str] | None = None) -> str:
    response = re.sub(r"[\u4e00-\u9fff]+", "", response).strip()

    response = re.sub(r"\[AGENT:\s*BUDGE?T?\w*\]", "[AGENT: BUDGET]", response, flags=re.I)
    response = re.sub(r"\[AGENT:\s*INVEST\w*\]", "[AGENT: INVESTMENT]", response, flags=re.I)
    response = re.sub(r"\[AGENT:\s*INVOIC\w*\]", "[AGENT: INVOICE]", response, flags=re.I)

    span = _last_brace_object_span(response)
    if not span:
        return response
    inner_start, inner_end = span
    end_after_brace = _extend_span_trailing_paren_after_brace(response, inner_end)
    start, end = _span_eat_wrapping_parens(response, inner_start, end_after_brace)
    blob = response[inner_start:inner_end]
    try:
        payload_raw = _parse_finmate_dict(blob)
        if not payload_raw:
            return response
        payload = {k: v for k, v in payload_raw.items() if k in VALID_KEYS}
        intent = str(payload.get("intent") or "").lower()
        investish = any(
            k in intent
            for k in (
                "invest",
                "portfolio",
                "market",
                "equity",
                "fund",
                "allocation",
                "trade",
                "etf",
                "stock",
            )
        )
        invest_forced = bool(
            tools_fallback is not None
            and any("yfinance" in str(x).lower() for x in tools_fallback)
        )
        if tools_fallback is not None:
            t_fallback = list(tools_fallback)
        else:
            t_fallback = ["yfinance_lookup"] if investish else ["list_transactions", "set_budget"]
        tn = _normalize_tools_needed(payload.get("tools_needed"), fallback=t_fallback)
        if invest_forced:
            has_yf = any("yfinance" in str(t).lower() for t in tn)
            only_budget = bool(tn) and set(tn) <= _BUDGET_TOOL_IDS
            if only_budget or not has_yf:
                tn = list(tools_fallback or ["yfinance_lookup"])
        payload["tools_needed"] = tn
        if invest_forced:
            vague = intent in ("suggestion", "suggest", "generic", "advice", "help", "")
            if vague or (not investish and "suggestion" in intent):
                payload["intent"] = "portfolio_suggestion"
            elif not investish and not vague:
                payload["intent"] = "investment_info"
        elif intent in (
            "ration",
            "ratio",
            "suggestion",
            "suggest",
            "generic",
            "advice",
            "help",
            "",
        ):
            payload["intent"] = "budget_plan"
        if payload.get("notes") is None:
            payload["notes"] = ""
        prefer_invest_steps = invest_forced or (
            (tools_fallback is not None and any("yfinance" in str(x).lower() for x in tools_fallback))
            or investish
        )
        step_fb = STEPS_FALLBACK_INVEST if prefer_invest_steps else STEPS_FALLBACK_BUDGET
        payload["steps"] = _normalize_steps(payload.get("steps"), fallback=step_fb)
        fixed = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        response = response[:start] + fixed + response[end:]
    except Exception:
        pass

    return response


def route_key_from_reply(text: str) -> str:
    """Map assistant text to orchestrator agent keys."""
    head = text.strip()[:500].upper()
    if "[AGENT: INVOICE]" in head:
        return "invoice_generator"
    if "[AGENT: INVESTMENT]" in head:
        return "investment_analyser"
    return "budget_planner"


def extract_planned_steps(text: str) -> list[str]:
    js_line = _extract_json_line(text)
    if not js_line:
        return []
    try:
        return list(json.loads(js_line).get("steps") or [])
    except json.JSONDecodeError:
        return []


def llm_available() -> bool:
    try:
        root = _resolve_lora_root()
        _find_adapter_dir(root)
        return True
    except FileNotFoundError:
        return False


def ensure_investment_reply_shape(reply: str) -> str:
    """If the model skipped the tag or wrapped JSON in parens, emit [AGENT: INVESTMENT] + prose + JSON."""
    tag = "[AGENT: INVESTMENT]"
    s = reply.strip()
    head = s[:220].upper()
    has_tag = tag in head or "[AGENT: INVEST" in head
    span = _last_brace_object_span(s)

    if span and not has_tag:
        inner_s, inner_e = span
        start_eat, end_eat = _span_eat_wrapping_parens(s, inner_s, inner_e)
        prefix = s[:start_eat].strip()
        prefix_sig = re.sub(r"[\s().]+", "", prefix)
        blob = s[inner_s:inner_e]
        parsed = _parse_finmate_dict(blob)
        step_fb = list(STEPS_FALLBACK_INVEST)
        steps = _normalize_steps(parsed.get("steps"), fallback=step_fb) if parsed else step_fb
        parts = [str(x).strip().rstrip(".") for x in steps[:4]]
        from_steps = (
            ". ".join(parts) + "."
            if parts
            else (
                "Consider low fees, diversification, and a time horizon you can stick to; use $TICKER if you want live data."
            )
        )
        if len(prefix_sig) > 40:
            prose_body = f"{prefix}\n\n{from_steps}"
        else:
            prose_body = from_steps
        s = f"{tag}\n\n{prose_body}\n\n{blob}"
    elif span is None and not has_tag:
        s = f"{tag}\n\n{s}"

    return _postprocess(s, tools_fallback=["yfinance_lookup"])


def ensure_budget_invoice_llm_reply_shape(reply: str) -> str:
    """Orchestrator / budget & invoice: add [AGENT] + prose when the model emits JSON only."""
    s = reply.strip()
    route = route_key_from_reply(s)
    tag = "[AGENT: INVOICE]" if route == "invoice_generator" else "[AGENT: BUDGET]"
    tools_fb = (
        ["list_transactions", "set_budget"]
        if route != "invoice_generator"
        else ["list_transactions", "render_invoice_pdf"]
    )
    step_fb = list(STEPS_FALLBACK_INVOICE if route == "invoice_generator" else STEPS_FALLBACK_BUDGET)
    head = s[:400].upper()
    has_tag = "[AGENT:" in head
    span = _last_brace_object_span(s)

    if span and not has_tag:
        inner_s, inner_e = span
        start_eat, _ = _span_eat_wrapping_parens(s, inner_s, inner_e)
        prefix = s[:start_eat].strip()
        prefix_sig = re.sub(r"[\s().]+", "", prefix)
        blob = s[inner_s:inner_e]
        parsed = _parse_finmate_dict(blob)
        steps = _normalize_steps(parsed.get("steps"), fallback=step_fb) if parsed else step_fb
        parts = [str(x).strip().rstrip(".") for x in steps[:4]]
        from_steps = (
            ". ".join(parts) + "."
            if parts
            else (
                "Review your income and fixed costs, then set a realistic spending cap you can stick to week by week."
                if route != "invoice_generator"
                else "Add clear line items with amounts, then check totals before generating the invoice PDF."
            )
        )
        if len(prefix_sig) > 40:
            prose_body = f"{prefix}\n\n{from_steps}"
        else:
            prose_body = from_steps
        s = f"{tag}\n\n{prose_body}\n\n{blob}"
    elif span is None and not has_tag:
        s = f"{tag}\n\n{s}"

    return _postprocess(s, tools_fallback=tools_fb)


def finalize_llm_reply(reply: str) -> str:
    """Call after `generate()` on the orchestrator path so replies match tag + prose + JSON."""
    route = route_key_from_reply(reply)
    if route == "investment_analyser":
        return ensure_investment_reply_shape(reply)
    return ensure_budget_invoice_llm_reply_shape(reply)


def generate(
    user_message: str,
    *,
    system_extra: str | None = None,
    json_tools_fallback: list[str] | None = None,
) -> str:
    if any(kw in user_message.lower() for kw in CRISIS_KEYWORDS):
        return (
            "[AGENT: BUDGET]\n\n"
            "This is a cash emergency - your first priority is covering rent, food, and utilities before anything else. "
            "Contact your bank about emergency options, and pause all non-essential spending right now. "
            "Reach out to family, friends, or local assistance programmes if needed to bridge the gap.\n\n"
            '{"intent":"budget_plan","steps":["Stop all non-essential spending immediately",'
            '"Contact bank about emergency overdraft or credit options","Cover rent and food first",'
            '"Seek emergency financial assistance if needed"],'
            '"tools_needed":["list_transactions","set_budget"],"notes":"crisis mode"}'
        )

    model, tokenizer, device = _load_model()

    import torch

    system_text = SYSTEM + (system_extra or "")
    if getattr(tokenizer, "chat_template", None):
        messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_message},
        ]
        prompt = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    else:
        prompt = (
            f"<|im_start|>system\n{system_text}\n"
            f"<|im_start|>user\n{user_message}\n"
            f"<|im_start|>assistant\n"
        )

    inputs = tokenizer(prompt, return_tensors="pt")
    if device == "cpu":
        inputs = {k: v.to(device) for k, v in inputs.items()}
    else:
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=min(settings.finmate_max_new_tokens, 512),
            repetition_penalty=1.15,
            do_sample=False,
        )

    new_tokens = outputs[0][inputs["input_ids"].shape[1] :]
    response = tokenizer.decode(new_tokens, skip_special_tokens=True)

    return _postprocess(response, tools_fallback=json_tools_fallback)


def clear_model_cache() -> None:
    _load_model.cache_clear()
