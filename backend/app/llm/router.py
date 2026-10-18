# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""模型分层路由：简单任务走小模型降本降延迟，复杂任务走大模型保质量。"""

from app.llm import registry

# 任务 -> 模型档位
_SMALL_TASKS = {"intent", "clarify", "chitchat", "route", "cache_match"}
_LARGE_TASKS = {"diagnose", "summarize", "rag_generate", "billing_explain"}


def model_for(task: str) -> str:
    """根据任务类型返回当前 chat provider 的模型 ID。未知任务默认大模型（偏保守）。"""
    tier = "small" if task in _SMALL_TASKS else "large"
    return registry.get_chat_provider().model_for(tier)
