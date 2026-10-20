"""Supervisor 只生成经过安全出口的结果及待执行意图；持久化由请求提交层负责。"""

import asyncio

from langgraph.graph import END, START, StateGraph

from app.access import require_identity
from app.agents import api_diagnostic, billing, doc_rag, intent_router, security, ticket
from app.agents.outcomes import AgentOutcome, decide
from app.agents.state import AgentState
from app.cache import route_cache, semantic_cache
from app.config import settings
from app.guardrail import desensitize
from app.llm import client
from app.llm.router import model_for
from app.memory import session
from app.observability.trace import TraceCollector, timer
from app.tools.registry import ToolContext, load_tools

load_tools()


def _finalize_result(result: dict) -> dict:
    result = desensitize.desensitize_obj(result)
    result["answer"] = security.review_output(result["answer"]).clean_text
    return result


async def _general_reply(query):
    result = await client.chat([
        {"role": "system", "content": "你是 API 平台技术支持助手。简短回答闲聊，并引导到技术支持问题。不得声称已经建单或转人工。"},
        {"role": "user", "content": query},
    ], model=model_for("chitchat"), temperature=0.5)
    return AgentOutcome(answer=result.content.strip(), tokens=result.total_tokens)


def build_graph(ctx: ToolContext, trace: TraceCollector):
    async def load_context(state):
        if "history" in state:
            return {}
        return {"history": await session.get_history(state["conversation_id"]),
                "collected_entities": await session.get_entities(state["conversation_id"])}

    async def intent_node(state):
        history = state.get("history", [])
        cached = None
        if not history:
            try:
                cached = await asyncio.wait_for(route_cache.get(state["query"]), settings.cache_timeout_seconds)
            except desensitize.SensitiveDataError:
                raise
            except Exception:
                trace.step("route_cache", status="error", error="路由缓存读取失败")
        with timer() as t:
            res = {**cached, "tokens": 0} if cached else await intent_router.classify(state["query"], history)
        merged = dict(state.get("collected_entities", {}))
        merged.update({key: value for key, value in res.get("entities", {}).items()
                       if value not in (None, "", [], {})})
        merged = desensitize.desensitize_obj(merged)
        trace.step("intent_router", input_summary=state["query"],
                   output_summary=f"intent={res['intent']}", duration_ms=t.ms,
                   token_usage=res.get("tokens", 0))
        clarify = ""
        if res["intent"] == "api_error" and not merged.get("request_id") and not merged.get("error_code"):
            clarify = "请提供 request_id、出错接口或错误码，帮助定位问题。"
        elif res["confidence"] < settings.intent_confidence_threshold and res["intent"] != "chitchat":
            clarify = "请补充具体场景：接口报错、账单费用，还是文档用法？"
        return {"intent": res["intent"], "confidence": res["confidence"],
                "entities": merged, "route": res["route"], "need_clarify": bool(clarify),
                "clarify_question": clarify,
                "route_cache_entry": res if not history and not cached else None}

    def after_intent(state):
        if state.get("need_clarify"):
            return "clarify"
        return "semantic_cache" if state["intent"] == "doc_qa" and not state.get("history") else "specialists"

    async def clarify_node(state):
        return {"final_answer": state["clarify_question"], "need_human": False}

    async def cache_node(state):
        try:
            cached, embedding = await semantic_cache.get(state["tenant_id"], state["query"])
        except desensitize.SensitiveDataError:
            raise
        except Exception:
            trace.step("semantic_cache", status="error", error="答案缓存读取失败")
            cached, embedding = None, None
        if cached:
            return {"final_answer": cached["answer"], "rag_citations": cached.get("citations", []),
                    "card": cached.get("card"), "need_human": False, "from_cache": True}
        return {"from_cache": False, "query_embedding": embedding}

    async def run_one(name, coro):
        with timer() as t:
            try:
                outcome = await coro
                if not isinstance(outcome, AgentOutcome):
                    raise TypeError("Agent 结果不符合契约")
            except desensitize.SensitiveDataError:
                raise
            except Exception:
                outcome = AgentOutcome(answer="", status="failed", reason="technical_failure")
        trace.step(name, input_summary="", output_summary=outcome.answer,
                   status="error" if outcome.status == "failed" else "ok",
                   error=outcome.reason if outcome.status == "failed" else None,
                   duration_ms=t.ms, token_usage=outcome.tokens)
        return name, outcome

    async def specialists_node(state):
        route, query, entities = state.get("route", []), state["query"], state.get("entities", {})
        jobs = []
        if not route:
            jobs.append(run_one("general", _general_reply(query)))
        if "api_diagnostic" in route:
            jobs.append(run_one("api_diagnostic", api_diagnostic.diagnose(
                query, entities, ctx, is_rate_limit=state["intent"] == "rate_limit")))
        if "billing" in route:
            jobs.append(run_one("billing", billing.handle(query, entities, ctx)))
        if "doc_rag" in route and not ("api_diagnostic" in route or "billing" in route):
            jobs.append(run_one("doc_rag", doc_rag.answer(query, history=state.get("history"))))
        outputs = dict(await asyncio.gather(*jobs))
        if not outputs and state["intent"] != "ticket":
            outputs["routing"] = AgentOutcome(answer="", status="failed", reason="invalid_route")
        action = decide(list(outputs.values()), requested_human=state["intent"] == "ticket")
        return {"agent_outputs": outputs,
                "rag_citations": [c for outcome in outputs.values() for c in outcome.citations],
                **action}

    async def ticket_node(state):
        if not state.get("pending_ticket"):
            return {"planned_ticket": None}
        outcomes = list(state.get("agent_outputs", {}).values())
        return {"planned_ticket": ticket.build_args(
            query=state["query"], intent=state["intent"], entities=state.get("entities", {}),
            ai_diagnosis="\n".join(o.answer for o in outcomes if o.status != "failed"),
            evidence={key: outcome.evidence for key, outcome in state.get("agent_outputs", {}).items()},
        )}

    async def summarize_node(state):
        outcomes = list(state.get("agent_outputs", {}).values())
        useful = [o for o in outcomes if o.status != "failed" and o.answer]
        draft = "\n\n".join(o.answer for o in useful) or "技术服务暂时无法完成诊断。"
        if useful and any(o.status == "failed" for o in outcomes):
            draft += "\n\n部分诊断暂未完成，以上仅包含已有证据支持的结果。"
        # 有业务动作时正文展示固定确认信息，避免卡片遮住实际动作状态。
        card = useful[0].card if len(useful) == 1 and not state.get("pending_ticket") else None
        return {"draft_answer": draft, "card": card}

    async def security_node(state):
        return {"final_answer": security.review_output(state["draft_answer"]).clean_text,
                "card": desensitize.desensitize_obj(state.get("card"))}

    g = StateGraph(AgentState)
    for name, node in (("load_context", load_context), ("intent", intent_node),
                       ("clarify", clarify_node), ("semantic_cache", cache_node),
                       ("specialists", specialists_node), ("ticket", ticket_node),
                       ("summarize", summarize_node), ("security", security_node)):
        g.add_node(name, node)
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "intent")
    g.add_conditional_edges("intent", after_intent, {
        "clarify": "clarify", "semantic_cache": "semantic_cache", "specialists": "specialists"})
    g.add_edge("clarify", END)
    g.add_conditional_edges("semantic_cache", lambda s: "cached" if s.get("from_cache") else "miss",
                            {"cached": END, "miss": "specialists"})
    for before, after in (("specialists", "ticket"), ("ticket", "summarize"),
                          ("summarize", "security"), ("security", END)):
        g.add_edge(before, after)
    return g.compile()


async def run(*, query, tenant_id, user_id, conversation_id, is_internal=False, role="",
              context=None, operation_id=""):
    ctx = ToolContext(tenant_id=tenant_id, user_id=user_id, role=role, operation_id=operation_id)
    require_identity(ctx)
    query = desensitize.desensitize_text(query)
    trace = TraceCollector(tenant_id=tenant_id, conversation_id=conversation_id)
    ctx.trace_id = trace.trace_id
    initial = {"tenant_id": tenant_id, "user_id": user_id, "conversation_id": conversation_id,
               "is_internal": ctx.is_internal, "query": query}
    if context is not None:
        initial.update(history=context["history"], collected_entities=context["entities"])
    try:
        final = await build_graph(ctx, trace).ainvoke(initial)
    except desensitize.SensitiveDataError:
        raise
    except Exception:
        trace.step("fallback", status="error", error="编排执行失败")
        final = {"final_answer": "技术服务暂时无法完成诊断。", "handoff_requested": True,
                 "planned_ticket": ticket.build_args(query=query, intent="ticket", entities={},
                    ai_diagnosis="", evidence={"pipeline_error": "编排执行失败"})}
    result = _finalize_result({
        "answer": final.get("final_answer", ""), "intent": final.get("intent"),
        "confidence": final.get("confidence", 0), "citations": final.get("rag_citations", []),
        "card": final.get("card"), "trace_id": trace.trace_id,
        "total_tokens": trace.total_tokens,
        **({"entities": final["entities"]} if "entities" in final else {}),
        "need_human": False, "ticket_id": None, "need_clarify": final.get("need_clarify", False),
        "from_cache": final.get("from_cache", False),
        "handoff_requested": final.get("handoff_requested", False),
        "planned_ticket": final.get("planned_ticket"),
    })
    effects = {"trace": {"trace_id": trace.trace_id, "steps": trace.steps},
               "cost": {"total_tokens": trace.total_tokens}}
    if final.get("route_cache_entry"):
        effects["route_cache"] = {"query": query, "result": final["route_cache_entry"]}
    outcomes = final.get("agent_outputs", {})
    if (final.get("intent") == "doc_qa" and not result["planned_ticket"]
            and not result["need_clarify"] and not result["from_cache"] and not final.get("history")
            and final.get("query_embedding") is not None
            and outcomes and all(o.status == "answered" for o in outcomes.values())):
        effects["semantic_cache"] = {"query": query, "result": result,
                                     "embedding": final["query_embedding"]}
    result["_effects"] = desensitize.desensitize_obj(effects)
    return result
