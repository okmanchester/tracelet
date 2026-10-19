"""合成敏感标记验证脱敏、模型、缓存、日志和图出口，不连接真实外部服务。"""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.agents import supervisor
from app.agents.ticket import TicketResult
from app.cache import route_cache, semantic_cache
from app.guardrail import desensitize
from app.guardrail.safe_logging import install_safe_logging
from app.llm import client, registry
from app.llm.base import ChatResult, LLMPermanentError
from app.memory import session
from app.observability.trace import TraceCollector

SECRETS = ["sk-aB9", "tinySecret", "fakeBearerToken", "13912345678", "pilot@example.test",
           "110101199001010019", "6222021234567890123", "0123456789abcdef0123456789abcdef"]
TEXT = ("key sk-aB9 secret_key=tinySecret Bearer fakeBearerToken 电话13912345678 "
        "pilot@example.test 身份证110101199001010019 卡6222021234567890123 "
        "签名0123456789abcdef0123456789abcdef request_id=req_20260615_8842 app_id=app_acme")


def assert_clean(value):
    text = json.dumps(value, ensure_ascii=False, default=str)
    for secret in SECRETS:
        assert secret not in text


@pytest.mark.parametrize("text", [
    TEXT, json.dumps({"content": TEXT}), "sk-a", "ak_x", '"api_key":"abc"', '"secret_key":"tinySecret"',
    "Bearer x", "我的手机13912345678请联系", "pilot@example.test", "prefix secret=abcd suffix",
])
def test_masking_is_irreversible_and_idempotent(text):
    clean = desensitize.desensitize_text(text)
    assert clean != text
    assert desensitize.desensitize_text(clean) == clean
    assert desensitize.detect(clean) == []
    assert_clean(clean)


@pytest.mark.parametrize("identifier", [
    "req_20260615_8842", "req_ffffffffffffffffffffffffffffffff", "app_acme", "key_acme",
    "tk_20261019_aa1133", "msg_aabbccddeeff", "trace_abcdef0123456789", "AUTH_KEY_EXPIRED",
])
def test_business_identifiers_remain_usable(identifier):
    assert desensitize.desensitize_text(identifier) == identifier


def test_nested_values_and_keys_cannot_bypass_by_claiming_to_be_ids():
    value = {"request_id": "req_sk-aB9", "meta": [{"pilot@example.test": TEXT}], "app_id": "app_acme"}
    clean = desensitize.desensitize_obj(value)
    assert_clean(clean)
    assert clean["app_id"] == "app_acme"
    assert desensitize.desensitize_obj(clean) == clean


def test_structured_opaque_secrets_numeric_pii_and_json_keep_no_original():
    value = {"secret_key": "tinySecret", "phone": 13912345678, "nested": [13912345678],
             "token": {"arbitrary": "fakeBearerToken"}, "app_id": "app_acme", "count": 10}
    clean = desensitize.desensitize_obj(value)
    assert_clean(clean)
    assert_clean(desensitize.desensitize_text(json.dumps(value)))
    assert clean["app_id"] == "app_acme"
    assert clean["count"] == 10
    assert desensitize.desensitize_obj(clean) == clean


async def test_all_model_boundaries_clean_inputs_outputs_and_split_stream(monkeypatch):
    seen = []

    class Provider:
        def model_for(self, _tier):
            return "fake"

        async def chat(self, messages, **kwargs):
            seen.extend([messages, kwargs])
            return ChatResult(content=TEXT, tool_calls=[{"arguments": json.dumps({"content": TEXT})}])

        async def chat_stream(self, messages, **kwargs):
            seen.extend([messages, kwargs])
            yield "sk-"
            yield "aB9"

        async def embed_documents(self, texts):
            seen.append(texts)
            return [[1.0] * 1024 for _ in texts]

        async def embed_query(self, text):
            seen.append(text)
            return [1.0] * 1024

        async def rerank(self, query, documents, **kwargs):
            seen.extend([query, documents, kwargs])
            return [{"index": 0, "score": 0.9}]

    provider = Provider()
    for name in ("get_chat_provider", "get_embedding_provider", "get_rerank_provider"):
        monkeypatch.setattr(registry, name, lambda: provider)
    schema = {"description": TEXT, "parameters": {"properties": {"token": {"type": "string"}}}}
    result = await client.chat([{"role": "user", "content": TEXT}], tools=[schema])
    assert seen[1]["tools"][0]["parameters"]["properties"]["token"] == {"type": "string"}
    assert_clean(result.content)
    assert_clean(result.tool_calls)
    streamed = [part async for part in client.chat_stream([{"role": "user", "content": TEXT}])]
    assert_clean(streamed)
    assert "sk-aB9" not in "".join(streamed)
    await client.embed_documents([TEXT])
    await client.embed_query(TEXT)
    await client.rerank(TEXT, [TEXT])
    assert_clean(seen)
    assert "req_20260615_8842" in str(seen)


async def test_sanitizer_failure_never_calls_provider(monkeypatch):
    provider = AsyncMock()
    monkeypatch.setattr(registry, "get_chat_provider", lambda: provider)

    def broken(_obj, **_kwargs):
        raise RuntimeError(TEXT)

    monkeypatch.setattr(desensitize, "_clean", broken)
    with pytest.raises(desensitize.SensitiveDataError) as caught:
        await client.chat([{"role": "user", "content": TEXT}])
    assert_clean(str(caught.value))
    provider.chat.assert_not_awaited()


async def test_provider_errors_do_not_expose_payload(monkeypatch):
    provider = SimpleNamespace(model_for=lambda _tier: "fake", chat=AsyncMock(side_effect=RuntimeError(TEXT)))
    monkeypatch.setattr(registry, "get_chat_provider", lambda: provider)
    with pytest.raises(LLMPermanentError) as caught:
        await client.chat([{"role": "user", "content": "safe"}])
    assert_clean(str(caught.value))


def test_log_factory_cleans_arguments_and_omits_exception_payload(monkeypatch, caplog):
    previous = logging.getLogRecordFactory()
    install_safe_logging()
    try:
        with caplog.at_level(logging.WARNING):
            logging.getLogger("pilot-test").warning("payload=%s", TEXT)
            try:
                raise RuntimeError(TEXT)
            except RuntimeError:
                logging.getLogger("pilot-test").exception("failure %s", TEXT)
        assert_clean(caplog.text)
        assert "异常详情已省略" in caplog.text
    finally:
        logging.setLogRecordFactory(previous)


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **_kwargs):
        self.values[key] = value

    async def lrange(self, key, _start, _stop):
        return self.lists.get(key, [])

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, *_args):
        pass

    async def expire(self, *_args):
        pass


async def test_redis_history_entities_route_and_answer_cache_contain_only_clean_text(monkeypatch):
    redis = MemoryRedis()
    for module in (session, route_cache, semantic_cache):
        monkeypatch.setattr(module, "get_redis", lambda: redis)
    monkeypatch.setattr(semantic_cache, "get_embedding_provider", lambda: SimpleNamespace(fingerprint="fake"))
    monkeypatch.setattr(semantic_cache.client, "embed_query", AsyncMock(return_value=[1.0, 0.0]))
    await session.append_message("conv_probe", "user", TEXT)
    await session.update_entities("conv_probe", {"notes": TEXT, "request_id": "req_20260615_8842"})
    route = {"intent": "doc_qa", "route": ["doc_rag"], "entities": {"notes": TEXT}, "confidence": 1.0}
    await route_cache.put(TEXT, route)
    result = {"answer": TEXT, "citations": [{"doc_title": TEXT}], "card": {"conclusion": TEXT}, "intent": "doc_qa"}
    await semantic_cache.put("a", TEXT, result, [1.0, 0.0])
    assert_clean(redis.values)
    assert_clean(redis.lists)
    assert_clean(await session.get_history("conv_probe"))
    assert_clean(await session.get_entities("conv_probe"))
    assert_clean(await route_cache.get(TEXT))
    hit, _ = await semantic_cache.get("a", TEXT)
    assert hit is not None
    assert_clean(hit)


@pytest.mark.parametrize("mode", [
    "general", "clarify", "cache", "partial_failure", "all_failure", "ticket_success", "ticket_failure",
    "pipeline_failure", "sanitizer_failure",
])
async def test_real_graph_exits_clean_before_memory_trace_and_cache(monkeypatch, mode):
    traces = []
    review = Mock(wraps=supervisor.security.review_output)
    monkeypatch.setattr(supervisor.security, "review_output", review)

    def trace_factory(**kwargs):
        trace = TraceCollector(**kwargs)
        trace.persist = AsyncMock()
        traces.append(trace)
        return trace

    monkeypatch.setattr(supervisor, "TraceCollector", trace_factory)
    for name, result in (("get_history", []), ("get_entities", {}),
                         ("update_entities", {"request_id": "req_20260615_8842"}), ("append_message", None)):
        monkeypatch.setattr(session, name, AsyncMock(return_value=result))
    monkeypatch.setattr(route_cache, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(route_cache, "put", AsyncMock())
    cached = {"answer": TEXT, "citations": [{"doc_title": TEXT}], "card": {"conclusion": TEXT}, "similarity": 1.0}
    monkeypatch.setattr(semantic_cache, "get", AsyncMock(return_value=(cached if mode == "cache" else None, [1.0, 0.0])))
    monkeypatch.setattr(semantic_cache, "put", AsyncMock())
    monkeypatch.setattr(supervisor.cost, "record", AsyncMock())
    monkeypatch.setattr(client, "chat", AsyncMock(return_value=ChatResult(content=TEXT)))
    intent = "doc_qa" if mode in {"cache", "clarify"} else "api_error" if mode in {
        "partial_failure", "all_failure", "ticket_success", "ticket_failure",
    } else "chitchat"
    route = ["api_diagnostic", "billing"] if intent == "api_error" else []
    classify = AsyncMock(return_value={"intent": intent, "confidence": 0.1 if mode == "clarify" else 1.0,
                                      "entities": {}, "route": route, "tokens": 0})
    if mode == "pipeline_failure":
        classify.side_effect = RuntimeError(TEXT)
    if mode == "sanitizer_failure":
        classify.side_effect = desensitize.SensitiveDataError()
    monkeypatch.setattr(supervisor.intent_router, "classify", classify)
    result = SimpleNamespace(answer=TEXT, tokens=0, citations=[{"doc_title": TEXT}],
                             card={"conclusion": TEXT, "evidence": [TEXT], "steps": [TEXT]},
                             evidence={}, need_human=False, need_ticket=mode.startswith("ticket_"))
    diagnostic = AsyncMock(return_value=result)
    billing = AsyncMock(return_value=result)
    if mode in {"partial_failure", "all_failure"}:
        diagnostic.side_effect = RuntimeError(TEXT)
    if mode == "all_failure":
        billing.side_effect = RuntimeError(TEXT)
    monkeypatch.setattr(supervisor.api_diagnostic, "diagnose", diagnostic)
    monkeypatch.setattr(supervisor.billing, "handle", billing)
    create = AsyncMock(return_value=TicketResult(None if mode == "ticket_failure" else "tk_probe", TEXT))
    monkeypatch.setattr(supervisor.ticket, "create_from_context", create)
    if mode == "sanitizer_failure":
        with pytest.raises(desensitize.SensitiveDataError):
            await supervisor.run(query=TEXT, tenant_id="a", user_id="u_a1", conversation_id="c_probe", role="customer_dev")
        create.assert_not_awaited()
        session.append_message.assert_not_awaited()
        return
    output = await supervisor.run(query=TEXT, tenant_id="a", user_id="u_a1", conversation_id="c_probe", role="customer_dev")
    assert_clean(output)
    assert_clean(traces[0].steps)
    assert_clean(session.append_message.await_args_list)
    assert_clean(semantic_cache.put.await_args_list)
    assert_clean(route_cache.put.await_args_list)
    assert_clean(classify.await_args)
    assert review.call_count >= 1
    if mode in {"ticket_success", "ticket_failure", "pipeline_failure"}:
        create.assert_awaited_once()
        assert_clean(create.await_args)


async def test_missing_tool_identity_cannot_start_supervisor(monkeypatch):
    build = AsyncMock(side_effect=AssertionError("不能开始编排"))
    monkeypatch.setattr(supervisor, "build_graph", build)
    with pytest.raises(Exception) as caught:
        await supervisor.run(query="safe", tenant_id="a", user_id="u_a1", conversation_id="c_probe", is_internal=True)
    assert caught.value.status_code == 403
    build.assert_not_called()


async def test_http_errors_validation_and_sanitizer_failures_do_not_echo_input():
    import httpx
    from app.main import app as production_app
    from app.schemas.chat import FeedbackRequest
    from fastapi import FastAPI, HTTPException

    app = FastAPI(exception_handlers=production_app.exception_handlers)

    @app.get("/http-error")
    async def http_error():
        raise HTTPException(400, TEXT)

    @app.get("/unexpected")
    async def unexpected():
        raise RuntimeError(TEXT)

    @app.get("/sanitizer")
    async def sanitizer():
        raise desensitize.SensitiveDataError()

    @app.post("/validate")
    async def validate(body: FeedbackRequest):
        return {"ok": True}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        for path, status in (("/http-error", 400), ("/unexpected", 500), ("/sanitizer", 500)):
            response = await c.get(path)
            assert response.status_code == status
            assert_clean(response.json())
        response = await c.post("/validate", json={"conversation_id": "c_probe", "type": TEXT})
        assert response.status_code == 422
        assert_clean(response.json())
