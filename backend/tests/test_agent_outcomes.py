"""真实编排图的结果契约：动作只作为意图返回，提交层另做事务验收。"""

from unittest.mock import AsyncMock

import pytest

from app.agents import supervisor
from app.agents.outcomes import AgentOutcome


@pytest.mark.parametrize("scenario", ["no_hit", "all_failed", "partial_failed", "ticket", "human", "clarify"])
async def test_graph_has_deterministic_outcome_and_never_writes_ticket(monkeypatch, scenario):
    intent = "doc_qa" if scenario == "no_hit" else "api_error"
    route = ["doc_rag"] if scenario == "no_hit" else ["api_diagnostic", "billing"]
    entities = {} if scenario == "clarify" else {"error_code": "SIGN_INVALID"}
    monkeypatch.setattr(supervisor.intent_router, "classify", AsyncMock(return_value={
        "intent": intent, "route": route, "entities": entities, "confidence": 1.0, "tokens": 0,
    }))
    diagnostic = AsyncMock(return_value=AgentOutcome(answer="有证据的诊断结果", need_ticket=scenario == "ticket"))
    billing = AsyncMock(return_value=AgentOutcome(answer="有证据的账单解释", need_human=scenario == "human"))
    if scenario in {"partial_failed", "all_failed"}:
        diagnostic.side_effect = RuntimeError("synthetic")
    if scenario == "all_failed":
        billing.side_effect = RuntimeError("synthetic")
    monkeypatch.setattr(supervisor.api_diagnostic, "diagnose", diagnostic)
    monkeypatch.setattr(supervisor.billing, "handle", billing)
    monkeypatch.setattr(supervisor.doc_rag, "answer", AsyncMock(return_value=AgentOutcome(
        answer="现有文档没有命中，请补充问题或选择人工支持。", hit=False)))
    write = AsyncMock(side_effect=AssertionError("编排阶段不得创建工单"))
    monkeypatch.setattr(supervisor.ticket, "create_from_context", write)
    result = await supervisor.run(query="safe", tenant_id="a", user_id="u_a", role="customer_dev",
        conversation_id="conv_safe", context={"history": [{"role": "user", "content": "previous"}], "entities": {}})
    write.assert_not_awaited()
    assert result["need_human"] is False
    if scenario == "no_hit":
        assert "没有命中" in result["answer"] and result["planned_ticket"] is None
    elif scenario == "all_failed":
        assert result["handoff_requested"] is True and result["planned_ticket"]
    elif scenario == "partial_failed":
        assert "账单解释" in result["answer"] and "部分诊断" in result["answer"]
        assert result["planned_ticket"] is None
    elif scenario == "ticket":
        assert result["planned_ticket"] and not result["handoff_requested"]
    elif scenario == "human":
        assert result["planned_ticket"] and result["handoff_requested"]
    else:
        assert result["need_clarify"] and result["planned_ticket"] is None
