from fastapi import APIRouter

from app.agents.types import AgentName

router = APIRouter()


@router.get("", response_model=list[dict[str, str]])
def list_agents() -> list[dict[str, str]]:
    """Document the three FinMate specialists for your report and Swagger."""
    return [
        {
            "name": AgentName.BUDGET_PLANNER.value,
            "title": "Budget Planner",
            "description": "Aggregates spending from Postgres and proposes budget-style guidance.",
        },
        {
            "name": AgentName.INVOICE_GENERATOR.value,
            "title": "Invoice Generator",
            "description": "Builds invoice drafts from user input; extend with PDF and persistence.",
        },
        {
            "name": AgentName.INVESTMENT_ANALYSER.value,
            "title": "Investment Analyser",
            "description": "Resolves tickers and (stub) analysis; wire Yahoo Finance / Alpha Vantage here.",
        },
    ]
