import uuid
from typing import List, Literal, Union

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

from app.core.response import BizException, Code, Result
from app.service.chat import handle_chat
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
