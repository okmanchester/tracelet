# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""Security Agent：最终回复前的安全审查与脱敏（强制执行）。

- 对最终输出做敏感信息脱敏（API Key/Secret/Token/手机号/邮箱/身份证/签名）。
- 标注检测到的敏感类型，写入安全事件（可观测）。
"""

import json
import re
from dataclasses import dataclass, field

from app.guardrail import desensitize

# 业务动作只允许提交层的固定确认模板表达。保守拦截完成/执行承诺话术，
# 不拦截“没有命中，可选择人工支持”等能力说明。
_ACTION_LANGUAGE = re.compile(
    r"(?:已|正在|将|会为|为您|帮您|替您).{0,24}(?:工单|建单|转.{0,8}人工|人工.{0,8}(?:接管|处理|支持|服务|客服))|"
    r"(?:工单|人工).{0,24}(?:已|创建成功|提交成功|转接成功|接管成功)|"
    r"(?:转接|转交|转给|转入|转到|转至|接通).{0,8}(?:人工|客服|工程师)|"
    r"(?:create|creat(?:ed|ing)|open(?:ed|ing)?|rais(?:e|ed|ing)|submit(?:ted|ting)?)"
    r".{0,40}(?:ticket|case)|(?:ticket|case).{0,40}(?:creat|open|rais|submit)|"
    r"handoff|hand.over|(?:transfer|escalat|connect|rout).{0,40}(?:human|agent|support)",
    re.IGNORECASE,
)


def has_action_language(result: dict) -> bool:
    """只检查会展示的生成内容；结果中的真实动作字段由提交层另行生成。"""
    return bool(_ACTION_LANGUAGE.search(json.dumps(
        {key: result.get(key) for key in ("answer", "card", "citations")}, ensure_ascii=False)))


@dataclass
class SecurityResult:
    clean_text: str
    sensitive_found: list[str] = field(default_factory=list)


def review_output(text: str) -> SecurityResult:
    found = desensitize.detect(text)
    clean = desensitize.desensitize_text(text)
    return SecurityResult(clean_text=clean, sensitive_found=found)
