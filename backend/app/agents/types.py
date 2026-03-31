from dataclasses import dataclass, field
from enum import Enum


class AgentName(str, Enum):
    """FinMate specialized agents — swap stubs for LLM calls in each module."""

    BUDGET_PLANNER = "budget_planner"
    INVOICE_GENERATOR = "invoice_generator"
    INVESTMENT_ANALYSER = "investment_analyser"


@dataclass
class AgentResult:
    agent: AgentName
    reply: str
    planned_steps: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
