
"""
统一响应与全局异常处理说明

1. 成功响应
   接口正常执行完成时，直接返回 Result.success(data)。
   HTTP 状态码固定为 200，响应体中的 code 为 0。

   示例：
       return Result.success()
       return Result.success({"id": 1})

   返回格式：
       {
           "code": 0,
           "message": "success",
           "data": ...
       }

2. 业务失败响应
   业务代码中不要手动返回 Result.fail，统一抛 BizException。
   HTTP 状态码、业务错误码、默认错误信息都从 Code 中取得。

   示例：
       raise BizException(Code.USER_NOT_FOUND)
       raise BizException(Code.USER_NOT_FOUND, "The specified user does not exist")

   返回格式：
       {
           "code": Code.USER_NOT_FOUND.biz_code,
           "message": Code.USER_NOT_FOUND.message 或自定义英文 message,
           "data": null
       }

3. 自动失败响应
   参数校验失败、404、405、其他 HTTPException、未捕获系统异常都会自动转换为
   Result 格式；校验详情和系统异常详情只记录日志，不返回给前端。
"""

from enum import Enum
from typing import Generic, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
import logging

from pydantic import BaseModel
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | "
    "%(name)s:%(lineno)d | %(message)s"
)

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)


# Code 响应码
class Code(Enum):
    SUCCESS = (200, 0, "success")
    PARAM_ERROR = (422, 40000, "Invalid request parameters")
    HTTP_ERROR = (400, 40099, "Bad request")
    NOT_FOUND = (404, 40400, "Resource not found")
    METHOD_NOT_ALLOWED = (405, 40500, "Method not allowed")
    SERVER_ERROR = (500, 1, "server error")

    UNAUTHORIZED = (401, 10001, "unauthorized")
    FORBIDDEN = (403, 10002, "forbidden")

    def __init__(self, http_status: int, biz_code: int, message: str):
        self.http_status = http_status
        self.biz_code = biz_code
        self.message = message


# Result 统一返回结果
T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    code: int
    message: str | None = None
    data: T | None = None

    @classmethod
    def success(cls, data: T | None = None):
        return cls(
            code=Code.SUCCESS.biz_code,
            message=Code.SUCCESS.message,
            data=data
        )

    @classmethod
    def fail(cls, code: Code, message: str | None = None):
        return cls(
            code=code.biz_code,
            message=message or code.message,
            data=None
        )


class BizException(Exception):
    def __init__(
        self,
        code: Code,
        message: str | None = None,
    ):
        self.code = code
        self.biz_code = code.biz_code
        self.http_status = code.http_status
        self.message = message or code.message

        super().__init__(self.message)


def get_http_exception_code(exc: StarletteHTTPException) -> Code:
    if exc.status_code == Code.NOT_FOUND.http_status:
        return Code.NOT_FOUND

    if exc.status_code == Code.METHOD_NOT_ALLOWED.http_status:
        return Code.METHOD_NOT_ALLOWED

    return Code.HTTP_ERROR


async def biz_exception_handler(
    request: Request,
    exc: BizException
):
    logger.warning(
        "业务异常 method=%s path=%s code=%s message=%s",
        request.method,
        request.url.path,
        exc.biz_code,
        exc.message
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=Result.fail(exc.code, exc.message).model_dump()
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
):
    logger.warning(
        "参数异常 method=%s path=%s errors=%s",
        request.method,
        request.url.path,
        exc.errors()
    )
    return JSONResponse(
        status_code=Code.PARAM_ERROR.http_status,
        content=Result.fail(Code.PARAM_ERROR).model_dump()
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException
):
    code = get_http_exception_code(exc)
    logger.warning(
        "HTTP异常 method=%s path=%s status=%s message=%s",
        request.method,
        request.url.path,
        exc.status_code,
        code.message
    )
    return JSONResponse(
        status_code=code.http_status,
        content=Result.fail(code).model_dump()
    )


async def global_exception_handler(
    request: Request,
    exc: Exception
):
    logger.exception(
        "系统异常 method=%s path=%s",
        request.method,
        request.url.path
    )
    return JSONResponse(
        status_code=Code.SERVER_ERROR.http_status,
        content=Result.fail(Code.SERVER_ERROR).model_dump()
    )


# 注册全局异常处理器
def register_exception_handlers(app: FastAPI):
    app.add_exception_handler(BizException, biz_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
