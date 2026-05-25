import uuid
from typing import List, Literal, Union

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

from app.core.response import BizException, Code, Result
from app.service.chat import chat_event_generator, handle_chat, validate_chat_event_subscription
from app.service.tenjudge_server import get_current_user_id

router = APIRouter()


class CodeAttachment(BaseModel):
    type: Literal["code"]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

class SubmissionAttachment(BaseModel):
    type: Literal["submission"]
    submission_id: int = Field(gt=0)

class ProblemAttachment(BaseModel):
    type: Literal["problem"]
    problem_id: int = Field(gt=0)

Attachment = Annotated[
    Union[
        CodeAttachment,
        SubmissionAttachment,
        ProblemAttachment,
    ],
    Field(discriminator="type")
]

class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    turn_index: int | None = None
    attachments: List[Attachment] = Field(default_factory=list)

class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    task_id: uuid.UUID


@router.post("/agent/chat")
async def chat(request: ChatRequest, token: str | None = Header(default=None, alias="tenjudge-token")):
    if not token:
        raise BizException(Code.UNAUTHORIZED)

    # 鉴权
    user_id = await get_current_user_id(token)
    if user_id is None:
        raise BizException(Code.UNAUTHORIZED)

    # 写数据库+发送异步Agent任务
    chat_response = await handle_chat(request, token, user_id)

    return Result.success(ChatResponse.model_validate(chat_response))


@router.get("/agent/chat/{task_id}/events")
async def chat_events(
        task_id: uuid.UUID,
        token: str | None = Header(default=None, alias="tenjudge-token"),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    if not token:
        raise BizException(Code.UNAUTHORIZED)

    # 鉴权
    user_id = await get_current_user_id(token)
    if user_id is None:
        raise BizException(Code.UNAUTHORIZED)

    # 返回 StreamingResponse 前完成任务归属校验，保证业务错误仍走统一 JSON 响应。
    await validate_chat_event_subscription(task_id, user_id, last_event_id)

    return StreamingResponse(
        chat_event_generator(task_id, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
