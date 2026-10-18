"""本机 HTTP 适配层；不实现检索、阈值或政策判断规则。"""

from contextlib import asynccontextmanager
import os
import re
from typing import Literal

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from service_resources import build_service

TEST_DB_ENV = "POLICY_API_TEST_DB_PATH"
UNAVAILABLE = "问答服务暂不可用，请检查本地资源。"
FAILED = "问答处理失败，请稍后重试。"


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: StrictStr

    @field_validator("question")
    @classmethod
    def validate_question(cls, value):
        value = value.strip()
        if not value or len(value) > 2000:
            raise ValueError("问题须为1至2000字符")
        return value


class PublicPolicy(BaseModel):
    policy_id: str
    source: str
    content: str


class AskResponse(BaseModel):
    status: Literal["ok", "guarded", "needs_input", "no_policy", "error"]
    answer: str
    policies: list[PublicPolicy]
    message: str


def public_text(value):
    """隐藏常见绝对路径；不是通用个人信息或密钥识别器。"""
    text = str(value or "")
    # 带空格的引号路径先整体处理，再处理未加引号的路径。
    text = re.sub(r'''(["'])(?:[A-Za-z]:[\\/]|\\\\|/)[^\r\n]*?\1''',
                  "[本地路径已隐藏]", text)
    text = re.sub(r"(?:[A-Za-z]:[\\/]|\\\\)[^\r\n，；。]*", "[本地路径已隐藏]", text)
    return re.sub(r"(?<![\w:/])/(?!/)[^\s\n，；。]+", "[本地路径已隐藏]", text)


def error_response(code, message):
    return JSONResponse(status_code=code, content={
        "status": "error", "answer": "", "policies": [], "message": message})


def configured_service():
    path = os.environ.get(TEST_DB_ENV, "").strip()
    if not path:
        raise ValueError("必须显式指定独立数据库副本")
    return build_service(path)


def create_app(service_factory=None):
    factory = configured_service if service_factory is None else service_factory

    @asynccontextmanager
    async def lifespan(application):
        application.state.service = None
        try:
            application.state.service = factory()
        except Exception:
            # 不输出路径、异常内容或请求正文；允许 health 报告未就绪。
            pass
        try:
            yield
        finally:
            application.state.service = None

    application = FastAPI(title="本地企业政策 API", lifespan=lifespan)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # 默认校验详情可能包含原请求内容，统一返回固定提示。
        return error_response(422, "请求不合法：仅接受question字符串，去除两端空白后须为1至2000字符。")

    @application.get("/health")
    def health():
        initialized = application.state.service is not None
        return JSONResponse(status_code=200 if initialized else 503, content={
            "status": "ok" if initialized else "unavailable",
            "service_initialized": initialized})

    @application.post("/ask", response_model=AskResponse)
    def ask(payload: AskRequest):
        service = application.state.service
        if service is None:
            return error_response(503, UNAVAILABLE)
        try:
            result = service.answer_question(payload.question)
            if result["status"] == "error":
                return error_response(503, UNAVAILABLE)
            # 明确白名单；绝不读取 final_policies 或调试字段作为引用。
            return AskResponse(
                status=result["status"], answer=public_text(result["answer"]),
                policies=[PublicPolicy(**{key: public_text(policy[key])
                          for key in ("policy_id", "source", "content")})
                          for policy in result["policies"]],
                message=public_text(result.get("message", "")))
        except Exception:
            return error_response(500, FAILED)

    return application


app = create_app()
