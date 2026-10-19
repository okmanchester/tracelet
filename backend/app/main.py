# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""FastAPI 应用入口。"""

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, chat, conversations, docs, tickets, traces, workbench
from app.api import eval as eval_api
from app.config import settings
from app.guardrail import desensitize
from app.guardrail.safe_logging import install_safe_logging

install_safe_logging()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("devsupport")

app = FastAPI(
    title="DevSupport AI",
    description="面向 API 开放平台的多 Agent 智能技术支持系统",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(desensitize.SensitiveDataError)
async def unsafe_content_error(_request, _exc):
    return JSONResponse(status_code=500, content={"detail": "内容安全处理失败，请稍后重试"})


@app.exception_handler(StarletteHTTPException)
async def http_error(_request, exc):
    return JSONResponse(status_code=exc.status_code,
                        content=desensitize.desensitize_obj({"detail": exc.detail}), headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, _exc):
    return JSONResponse(status_code=422, content={"detail": "请求参数无效"})


@app.exception_handler(Exception)
async def service_error(_request, _exc):
    return JSONResponse(status_code=500, content={"detail": "服务暂时不可用，请稍后重试"})


@app.get("/health", tags=["system"])
@app.get("/api/health", tags=["system"])
async def health() -> dict:
    """健康检查。"""
    return {"status": "ok", "env": settings.app_env, "version": app.version}


app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(docs.router)
app.include_router(tickets.router)
app.include_router(workbench.router)
app.include_router(traces.router)
app.include_router(eval_api.router)
