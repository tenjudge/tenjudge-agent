import uuid
from typing import List, Literal, Union

from fastapi import APIRouter, Header, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

from app.core.response import BizException, Code, Result
from app.service.chat import chat_event_generator, handle_chat, validate_chat_event_subscription
from app.service.tenjudge_server import get_current_user_id

router = APIRouter(tags=["Agent Chat"])


class CodeAttachment(BaseModel):
    type: Literal["code"] = Field(description="附件类型。")
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        description="源代码文本。后端会统一规范化换行符。",
    )

class SubmissionAttachment(BaseModel):
    type: Literal["submission"] = Field(description="附件类型。")
    submission_id: int = Field(gt=0, description="要附加的 TenJudge 提交 ID。")

class ProblemAttachment(BaseModel):
    type: Literal["problem"] = Field(description="附件类型。")
    problem_id: int = Field(gt=0, description="要附加的 TenJudge 题目 ID。")

Attachment = Annotated[
    Union[
        CodeAttachment,
        SubmissionAttachment,
        ProblemAttachment,
    ],
    Field(discriminator="type")
]

class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description="已有会话 ID。不传表示创建新会话。",
    )
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        description="本轮用户消息。",
    )
    turn_index: int | None = Field(
        default=None,
        description=(
            "已有会话的可选重启轮次。"
            "传入后，后端会先删除该轮及之后的消息和任务，再创建新的当前轮。"
        ),
    )
    attachments: List[Attachment] = Field(
        default_factory=list,
        description="本轮可选附件，支持题目、提交记录或原始代码。",
    )

class ChatResponse(BaseModel):
    conversation_id: uuid.UUID = Field(description="会话 ID，后续继续对话时传入。")
    task_id: uuid.UUID = Field(description="异步智能体任务 ID，用于订阅 SSE 事件流。")


@router.post(
    "/chat",
    response_model=Result[ChatResponse],
    summary="Submit an agent chat turn",
    description=(
        "为一轮对话创建异步智能体任务并立即返回。"
        "客户端随后应订阅 GET /chat/{task_id}/events，接收进度和回答片段。"
        "创建新会话时不传 conversation_id；继续已有会话时传 conversation_id；"
        "如果要从历史轮次重新开始，传 conversation_id 和 turn_index。"
    ),
)
async def chat(
        request: ChatRequest,
        token: str | None = Header(default=None, alias="tenjudge-token", description="TenJudge 鉴权 token。"),
):
    if not token:
        raise BizException(Code.UNAUTHORIZED)

    # 鉴权
    user_id = await get_current_user_id(token)
    if user_id is None:
        raise BizException(Code.UNAUTHORIZED)

    # 写数据库+发送异步Agent任务
    chat_response = await handle_chat(request, token, user_id)

    return Result.success(ChatResponse.model_validate(chat_response))


@router.get(
    "/chat/{task_id}/events",
    summary="Subscribe to agent task events",
    description=(
        "订阅某个智能体任务的 Server-Sent Events 事件流。"
        "事件流会把 Redis Stream ID 作为 SSE id 返回。"
        "支持的事件名为 progress、message、title、failed 和 done。"
        "data 字段始终是普通字符串。"
        "断线重连时可传 Last-Event-ID，值必须是此前从该事件流收到过的 SSE id。"
        "没有新事件时，服务端会发送 ': ping' 注释作为心跳。"
        "收到 done 事件表示任务事件流结束，连接随后关闭。"
        "鉴权失败、任务不属于当前用户、任务不存在、事件流过期等错误会在开始流式返回前以统一 JSON 响应返回。"
    ),
    responses={
        200: {
            "description": "SSE 事件流。每个事件包含 id、event 和 data 行。",
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "string",
                        "example": (
                            "id: 1-0\n"
                            "event: progress\n"
                            "data: Thinking\n\n"
                            "id: 2-0\n"
                            "event: message\n"
                            "data: Hello\n\n"
                            "id: 3-0\n"
                            "event: done\n"
                            "data: \n\n"
                        ),
                    },
                },
            },
        },
    },
)
async def chat_events(
        task_id: Annotated[uuid.UUID, Path(description="POST /chat 返回的异步智能体任务 ID。")],
        token: str | None = Header(default=None, alias="tenjudge-token", description="TenJudge 鉴权 token。"),
        last_event_id: str | None = Header(
            default=None,
            alias="Last-Event-ID",
            description="最后收到的事件的 SSE id，用于断线重连后续接事件流。",
        ),
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
