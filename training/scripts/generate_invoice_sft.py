import json, random

SYSTEM = (
    "You are FinMate, a helpful financial assistant.\n"
    "Rules for every reply:\n"
    "1) Start with exactly one line: [AGENT: BUDGET], [AGENT: INVESTMENT], or [AGENT: INVOICE].\n"
    "2) Then write 2-4 sentences in plain English: empathetic, specific, with reasoning (not only raw percentages).\n"
    "3) End with a single line of valid JSON (no code fences) with keys: intent, steps (array), tools_needed (array), notes (string, optional)."
)

CLIENTS = ["Acme Corp", "Bright Solutions", "Nova Digital", "Peak Ventures", "Streamline Ltd",
           "Blue Horizon", "Rapid Build Co", "Clarity Agency", "Apex Systems", "Urban Craft"]
SERVICES = ["web development", "graphic design", "consulting", "data analysis", "content writing",
            "SEO audit", "mobile app development", "cloud migration", "legal advisory", "accounting"]
TERMS = ["Net 15", "Net 30", "Net 45", "due on receipt"]

TEMPLATES = [
    lambda c, s, amt, term, hrs: (
        f"Draft an invoice for {c} for {s} work — {hrs} hours at ${amt/hrs:.0f}/hr.",
        f"I will structure this invoice for {c} with a clear line item for {s} ({hrs} hrs) and payment terms of {term}. "
        f"Itemised hourly invoices reduce client questions and get approved faster than lump-sum bills.",
        {"intent": "generate_invoice", "steps": [f"Add line item: {s} x {hrs} hrs", "Set payment terms to " + term, "Send to " + c], "tools_needed": ["invoice_generator"], "notes": f"total=${amt:.2f}"}
    ),
    lambda c, s, amt, term, hrs: (
        f"I need to invoice {c} ${amt:.2f} for {s}. Payment terms {term}.",
        f"A clean invoice for {c} should show the {s} scope, total of ${amt:.2f}, and {term} terms on a single page. "
        f"Adding a brief scope summary above the line items helps {c} match it to their PO without back-and-forth.",
        {"intent": "generate_invoice", "steps": ["List scope of " + s, f"Total: ${amt:.2f}", "Terms: " + term, "Deliver to " + c], "tools_needed": ["invoice_generator"], "notes": f"client={c}"}
    ),
    lambda c, s, amt, term, hrs: (
        f"Create a professional invoice for {s} delivered to {c}, total ${amt:.2f}.",
        f"I will generate a professional invoice for {c} covering {s} at ${amt:.2f} with {term} payment terms. "
        f"Including your bank details or payment link directly on the invoice cuts average payment time significantly.",
        {"intent": "generate_invoice", "steps": ["Draft invoice header for " + c, "Add " + s + " line item", "Attach payment instructions"], "tools_needed": ["invoice_generator"], "notes": f"terms={term}; amount=${amt:.2f}"}
    ),
    lambda c, s, amt, term, hrs: (
        f"My client {c} owes me for {s}. How do I invoice them for ${amt:.2f}?",
        f"To invoice {c} for {s}, list the deliverables with dates, total ${amt:.2f}, and specify {term} clearly. "
        f"If this is a recurring engagement, a numbered invoice series helps both sides track payment history without confusion.",
        {"intent": "generate_invoice", "steps": ["Number the invoice", "List " + s + " deliverables", f"State total ${amt:.2f} and {term}"], "tools_needed": ["invoice_generator"], "notes": f"client={c}; recurring=check"}
    ),
]

random.seed(42)
examples = []
for i in range(2000):
    c = random.choice(CLIENTS)
    s = random.choice(SERVICES)
    hrs = random.randint(5, 80)
    rate = random.choice([50, 75, 100, 125, 150, 200])
    amt = hrs * rate + random.randint(0, 500)
    term = random.choice(TERMS)
    tmpl = TEMPLATES[i % len(TEMPLATES)]
    user_msg, nl, payload = tmpl(c, s, amt, term, hrs)
    assistant = f"[AGENT: INVOICE]\n\n{nl}\n\n{json.dumps(payload, separators=(',', ':'))}"
    examples.append({"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": assistant}
    ]})

with open("data/_part_invoice.jsonl", "w") as f:
    for ex in examples:
        f.write(json.dumps(ex, ensure_ascii=False) + "\n")

print(f"Wrote {len(examples)} invoice examples")