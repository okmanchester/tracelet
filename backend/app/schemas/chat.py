# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""对话/工单相关 DTO。"""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class FeedbackRequest(BaseModel):
    conversation_id: str
    message_id: str | None = None
    type: str  # resolved / unresolved / need_human


class TicketUpdateRequest(BaseModel):
    status: str | None = None
    assignee: str | None = None
    note: str | None = None
