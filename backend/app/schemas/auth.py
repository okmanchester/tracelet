# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""认证相关 DTO。"""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class UserInfo(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: str
    tenant_id: str
    tenant_name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo
