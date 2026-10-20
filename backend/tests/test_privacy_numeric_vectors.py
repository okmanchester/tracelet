"""真实 BGE 分数曾被识别成银行卡尾数；保护数值及敏感字段两条边界。"""

import numpy as np
import pytest

from app.guardrail import desensitize
from app.llm import client, registry


def test_normalized_vectors_and_scores_survive_repeated_sanitizing():
    source = {"embedding": [0.9972416162490845, -0.5700253248214722, 0.0, 1.0],
              "score": 0.5700253248214722}
    once = desensitize.desensitize_obj(source)
    assert once == source
    assert desensitize.desensitize_obj(once) == source
    assert np.dot(np.array(once["embedding"]), np.array(source["embedding"])) > 0


@pytest.mark.parametrize("value", [13800138000, 13800138000.0, 6222020202020202,
                                   "0.6222020202020202"])
def test_sensitive_numeric_and_free_text_values_still_masked(value):
    assert desensitize.desensitize_obj(value) != value


@pytest.mark.parametrize("field", ["phone", "api_key", "signature", "token", "bankcard"])
def test_sensitive_fields_do_not_allow_small_floats(field):
    assert desensitize.desensitize_obj({field: 0.5700253248214722})[field].startswith("[已脱敏:")


async def test_realistic_rerank_precision_remains_usable(monkeypatch):
    class Provider:
        async def rerank(self, *_args, **_kwargs):
            return [{"index": 0, "score": 0.9972416162490845}]

    monkeypatch.setattr(registry, "get_rerank_provider", lambda: Provider())
    result = await client.rerank("回调如何排查", ["检查回调地址"])
    assert result[0]["score"] >= 0.3
