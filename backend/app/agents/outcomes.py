"""专业 Agent 共用结果契约；业务动作由服务端集中决策。"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class AgentOutcome:
    answer: str
    evidence: dict = field(default_factory=dict)
    citations: list[dict] = field(default_factory=list)
    need_ticket: bool = False
    need_human: bool = False
    tokens: int = 0
    card: dict | None = None
    status: Literal["answered", "no_hit", "insufficient", "failed"] = "answered"
    reason: str = ""
    hit: bool = True
    candidates: int = 0
    top_score: float = 0.0
    error_code: str | None = None

    def __post_init__(self):
        if not self.hit:
            self.status = "no_hit"


def decide(outcomes: list[AgentOutcome], *, requested_human=False):
    all_failed = bool(outcomes) and all(o.status == "failed" for o in outcomes)
    handoff = requested_human or all_failed or any(o.need_human for o in outcomes)
    return {"handoff_requested": handoff,
            "pending_ticket": handoff or any(o.need_ticket for o in outcomes)}
