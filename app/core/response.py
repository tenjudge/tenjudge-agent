
from enum import Enum
from typing import Generic, TypeVar

from fastapi import FastAPI, Request
import logging

from pydantic import BaseModel
from fastapi.responses import JSONResponse

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

# Code 业务码
class Code(Enum):
    SUCCESS = (0, "success")
    SERVER_ERROR = (1, "server error")

    USER_NOT_FOUND = (40001, "用户不存在")
    USER_ALREADY_EXISTS = (40002, "用户名已存在")
    ORDER_STATUS_INVALID = (50001, "订单状态不允许操作")
    BALANCE_NOT_ENOUGH = (50002, "余额不足")

    def __init__(self, code: int, message: str):
        self.code = code
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
            code=Code.SUCCESS.code,
            message=Code.SUCCESS.message,
            data=data
        )

    @classmethod
    def fail(cls, code: int, message: str):
        return cls(
            code=code,
            message=message,
            data=None
        )

class BizException(Exception):
    def __init__(
        self,
        code: Code,
        message: str | None = None,
    ):
        self.code = code.code
        if message is None:
            self.message = code.message
        else :
            self.message = message

        super().__init__(self.message)


async def biz_exception_handler(
    request: Request,
    exc: BizException
):
    logger.warning(
        "业务异常 method=%s path=%s code=%s message=%s",
        request.method,
        request.url.path,
        exc.code,
        exc.message
    )
    return JSONResponse(
        status_code=200,
        content=Result.fail(exc.code, exc.message).model_dump()
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
        status_code=500,
        content=Result.fail(
            Code.SERVER_ERROR.code,
            Code.SERVER_ERROR.message
        ).model_dump()
    )


# 注册全局异常处理器
def register_exception_handlers(app: FastAPI):
    app.add_exception_handler(BizException, biz_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)
