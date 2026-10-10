"""Generate a deterministic 120-case synthetic FinMate research benchmark."""
from __future__ import annotations
import json
from pathlib import Path
from collections import Counter

OUT = Path(__file__).with_name("benchmark.jsonl")
cases = []

# 40 numerical/data-grounded tasks: 20 recent transaction aggregates, 10 invoices,
# and 10 calculations in prompt text.
for i in range(20):
    amounts = [420+37*i, 310+19*i, 95+11*i]
    tx = [
      {"amount":amounts[0],"currency":"INR","category":"food","description":"Synthetic grocery transaction","days_ago":2},
      {"amount":amounts[1],"currency":"INR","category":"food","description":"Synthetic dining transaction","days_ago":9},
      {"amount":amounts[2],"currency":"INR","category":"food","description":"Synthetic food transaction","days_ago":21},
      {"amount":777+i,"currency":"INR","category":"transport","description":"Distractor category transaction","days_ago":4},
      {"amount":999+i,"currency":"INR","category":"food","description":"Stale transaction outside 30-day window","days_ago":58},
    ]
    cases.append({"case_id":f"NUM-TX-{i+1:02d}","group":"numerical","subgroup":"transaction_aggregation",
      "message":"How much did I spend on food in the last 30 days? Give the total and briefly explain it.",
      "expected_agent":"budget_planner","transactions_fixture":tx,"gold_numeric_values":[sum(amounts)],
      "tolerance":0.01,"requires_invoice_artifacts":False})
for i in range(10):
    a,b,c=49+13*i,79+17*i,25+7*i
    cases.append({"case_id":f"NUM-INV-{i+1:02d}","group":"numerical","subgroup":"invoice_subtotal",
      "message":f"Create an invoice with website maintenance INR {a}, SEO audit INR {b}, and domain renewal INR {c}. What is the subtotal before tax?",
      "expected_agent":"invoice_generator","gold_numeric_values":[a+b+c],"tolerance":0.01,
      "requires_invoice_artifacts":True,"gold_invoice":{"currency":"INR","subtotal":a+b+c,"line_amounts":[a,b,c]}})
for i in range(10):
    income,rent,emi,food,utilities=38000+2750*i,9000+375*i,2500+125*i,3000+110*i,1000+45*i
    remaining=income-rent-emi-food-utilities
    cases.append({"case_id":f"NUM-BAL-{i+1:02d}","group":"numerical","subgroup":"balance_arithmetic",
      "message":f"My monthly income is INR {income}, rent is INR {rent}, EMI is INR {emi}, groceries are INR {food}, and utilities are INR {utilities}. How much remains after these expenses?",
      "expected_agent":"budget_planner","gold_numeric_values":[remaining],"tolerance":0.01,
      "requires_invoice_artifacts":False})

# 40 memory-dependent tasks. These facts are seeded as source=research_eval, which is
# visible through semantic search but not recent-chat or onboarding-context helpers.
for i in range(40):
    income,rent,goal=46500+1375*i,12000+275*(i%17),7000+450*(i%19)
    risk=["conservative","moderate","aggressive","moderate"][i%4]
    horizon=["1 year","3 years","5 years","10 years"][i%4]
    variants=[
      ("income",f"The synthetic user's monthly after-tax income is INR {income}.",str(income),"budget_planner","From my saved profile, what is my monthly after-tax income?"),
      ("rent",f"The synthetic user's monthly rent is INR {rent}.",str(rent),"budget_planner","What monthly rent amount did I previously tell you?"),
      ("risk",f"The synthetic user's investment risk tolerance is {risk}.",risk,"investment_analyser","What investment risk tolerance did I record in my profile?"),
      ("horizon",f"The synthetic user's investment time horizon is {horizon}.",horizon,"investment_analyser","What investment time horizon did I previously specify?"),
      ("goal",f"The synthetic user's monthly savings goal is INR {goal}.",str(goal),"budget_planner","What monthly savings target did I ask you to remember?"),
    ]
    kind,relevant,target,agent,question=variants[i%len(variants)]
    fixtures=[relevant,
      f"Distractor: the synthetic user's preferred chart style is bar chart number {i%7}.",
      f"Distractor: the synthetic user's favorite reminder day is day {i%5+1} of the month.",
      f"Distractor: an unrelated note refers to invoice template version {i%4+1}.",
      f"Distractor: the synthetic user's preferred notification window is {8+i%8}:00 local time."]
    cases.append({"case_id":f"MEM-{kind.upper()}-{i+1:02d}","group":"memory","subgroup":f"memory_{kind}",
      "message":question,"expected_agent":agent,"memory_fixture":fixtures,"relevant_memory":relevant,
      "gold_memory_answer":target,"gold_numeric_values":[float(target)] if target.isdigit() else [],
      "tolerance":0.01,"requires_invoice_artifacts":False})

# 40 routing/orchestration cases: 20 single specialist plus 20 multi-specialist.
singles=[
("budget_planner","Summarize my recent spending by category and suggest a budget cap."),
("budget_planner","Help me plan savings from my income and recurring monthly expenses."),
("budget_planner","Review my grocery spending and identify where I can reduce costs."),
("investment_analyser","Review my portfolio allocation for a moderate-risk, long-term investor."),
("investment_analyser","Explain how diversification affects the risk of a personal portfolio."),
("investment_analyser","I have INR 15000 available for five years and a conservative risk profile. Discuss allocation options."),
("invoice_generator","Create an invoice draft for consulting at INR 1800 and hosting at INR 350."),
("invoice_generator","Prepare an itemized invoice for design work INR 2400 and maintenance INR 600."),
("invoice_generator","Generate an invoice with two line items: bookkeeping INR 750 and domain renewal INR 120."),
("budget_planner","Compare my stated monthly income with my rent, bills, and food budget."),
("investment_analyser","Explain the difference between risk tolerance and investment time horizon."),
("invoice_generator","Draft a client invoice for a website audit INR 900 and implementation INR 3200."),
("budget_planner","What changes could help me keep monthly expenses within a fixed cap?"),
("investment_analyser","What should I consider before choosing a diversified long-term investment allocation?"),
("invoice_generator","Create a professional invoice draft with line items for software setup INR 1200 and support INR 400."),
("budget_planner","I want to build an emergency fund. Help me plan monthly contributions."),
("investment_analyser","Explain a conservative portfolio approach without using live stock quotes."),
("invoice_generator","Prepare an invoice for training services INR 5000 and documentation INR 1500."),
("budget_planner","How can I set a realistic weekly spending limit from a monthly budget?"),
("investment_analyser","Describe factors to review before reallocating my investment portfolio."),
]
for i,(agent,msg) in enumerate(singles,1):
    cases.append({"case_id":f"ROUTE-SINGLE-{i:02d}","group":"routing","subgroup":"single_specialist",
      "message":msg,"expected_agent":agent,"requires_invoice_artifacts":agent=="invoice_generator"})

multi=[
("budget_investment","Analyze my spending and tell me how much I can invest this month.",["budget_planner","investment_analyser"]),
("budget_invoice","Analyze my expenses and generate an invoice for the listed expenses.",["budget_planner","invoice_generator"]),
("three_domain","Review my budget, suggest an investment allocation, and create an invoice for consulting INR 1200.",["budget_planner","investment_analyser","invoice_generator"]),
("budget_investment","Compare income against monthly spending, then discuss a cautious investment plan.",["budget_planner","investment_analyser"]),
("budget_invoice","Review my recent expenses and prepare a client invoice for website maintenance INR 800.",["budget_planner","invoice_generator"]),
("three_domain","Summarize my monthly budget, consider an investment for the surplus, and draft an invoice for design INR 950.",["budget_planner","investment_analyser","invoice_generator"]),
("budget_investment","Check my spending pattern and explain an appropriate investment allocation for the remaining money.",["budget_planner","investment_analyser"]),
("budget_invoice","Review my spending and make an invoice with travel support INR 600 and setup INR 300.",["budget_planner","invoice_generator"]),
("three_domain","Analyze expenses, estimate what I could invest, and create an invoice for consulting INR 2100.",["budget_planner","investment_analyser","invoice_generator"]),
("budget_investment","Look at the difference between my income and expenses, then discuss savings versus investing.",["budget_planner","investment_analyser"]),
("budget_invoice","Summarize my expenses and draft an invoice for maintenance INR 700 and support INR 250.",["budget_planner","invoice_generator"]),
("three_domain","Evaluate my monthly expenses, consider an investment approach, and draft an invoice for audit INR 500.",["budget_planner","investment_analyser","invoice_generator"]),
("budget_investment","How much room does my budget leave for investing, and what risk factors should I consider?",["budget_planner","investment_analyser"]),
("budget_invoice","Check my recent spending and create an invoice for technical assistance INR 1300.",["budget_planner","invoice_generator"]),
("three_domain","Review my budget, estimate investable surplus, and invoice a client for development INR 3600.",["budget_planner","investment_analyser","invoice_generator"]),
("budget_investment","Review my income and monthly categories, then advise on a cautious allocation for excess funds.",["budget_planner","investment_analyser"]),
("budget_invoice","Summarize monthly spending and prepare an invoice for configuration INR 400 plus support INR 500.",["budget_planner","invoice_generator"]),
("three_domain","Analyze household costs, explain an investment option for the remainder, and prepare a service invoice INR 2500.",["budget_planner","investment_analyser","invoice_generator"]),
("budget_investment","Review spending and estimate money available for long-term investing after expenses.",["budget_planner","investment_analyser"]),
("budget_invoice","Look at my spending and create an invoice for project work INR 1700 and revision INR 400.",["budget_planner","invoice_generator"]),
]
for i,(kind,msg,agents) in enumerate(multi,1):
    cases.append({"case_id":f"ROUTE-MULTI-{i:02d}","group":"routing","subgroup":kind,
      "message":msg,"expected_source":"agentic","expected_agents":agents,
      "requires_invoice_artifacts":"invoice_generator" in agents})

assert len(cases)==120 and len({c["case_id"] for c in cases})==120
OUT.write_text("\n".join(json.dumps(c,ensure_ascii=False) for c in cases)+"\n",encoding="utf-8")
print(f"Wrote {len(cases)} cases to {OUT}; groups={dict(Counter(c['group'] for c in cases))}")
