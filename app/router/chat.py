
from fastapi import APIRouter, Header

from app.core.response import BizException, Code, Result
from app.service.auth import get_current_user_id
from app.service.chat import ChatRequest, handle_chat

router = APIRouter()


@router.post("/agent/chat")
async def chat(request: ChatRequest, token: str | None = Header(default=None, alias="tenjudge-token")):
    if not token:
        raise BizException(Code.UNAUTHORIZED)

    # 鉴权
    user_id = await get_current_user_id(token)
    if user_id is None:
        raise BizException(Code.UNAUTHORIZED)

    # 写数据库+发送异步Agent任务
    await handle_chat(request, token, user_id)

    return Result.success() # TODO 返回conversation_id等信息
