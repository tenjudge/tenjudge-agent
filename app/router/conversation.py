import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, Path, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.core.response import BizException, Code, Result
from app.repository.conversations import Conversation, ConversationRepository
from app.repository.messages import MessageRepository
from app.repository.tasks import TaskRepository
from app.service.tenjudge_server import get_current_user_id


router = APIRouter(tags=["Agent Conversations"])

DEFAULT_CONVERSATION_PAGE_LIMIT = 20
MAX_CONVERSATION_PAGE_LIMIT = 100


class ConversationListItem(BaseModel):
    id: uuid.UUID = Field(description="会话 ID。继续该会话时作为 conversation_id 传入。")
    title: str | None = Field(default=None, description="生成的会话标题。null 表示标题尚未生成或不可用。")


class ConversationListResponse(BaseModel):
    items: list[ConversationListItem] = Field(description="当前用户的会话列表，按更新时间倒序排列。")
    next_cursor: str | None = Field(
        default=None,
        description="下一页游标。null 表示没有更多会话。",
    )


class ConversationMessageItem(BaseModel):
    turn_index: int = Field(description="消息所属的会话轮次，从 1 开始。")
    role: Literal["user", "agent"] = Field(description="消息角色。user 表示用户消息，agent 表示智能体消息。")
    content: str = Field(description="消息正文。")
    attachments: list[dict[str, Any]] = Field(description="用户消息附件。智能体消息通常为空数组。")


class ConversationDetailResponse(BaseModel):
    id: uuid.UUID = Field(description="会话 ID。")
    title: str | None = Field(default=None, description="生成的会话标题。null 表示标题尚未生成或不可用。")
    status: Literal["finished", "running"] = Field(description="会话状态。running 表示当前仍有智能体任务在执行。")
    running_task_id: uuid.UUID | None = Field(
        default=None,
        description="当前正在运行的任务 ID。仅当 status 为 running 且任务存在时返回，否则为 null。",
    )
    messages: list[ConversationMessageItem] = Field(description="会话历史消息，按轮次升序排列，同一轮中用户消息在智能体消息之前。")


def _encode_conversation_cursor(conversation: Conversation) -> str:
    payload = {
        "updated_at": conversation.updated_at.isoformat(),
        "id": str(conversation.id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_conversation_cursor(cursor: str | None) -> tuple[datetime | None, uuid.UUID | None]:
    if cursor is None:
        return None, None

    try:
        padded_cursor = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded_cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        updated_at = datetime.fromisoformat(payload["updated_at"])
        conversation_id = uuid.UUID(payload["id"])
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise BizException(Code.PARAM_ERROR, "cursor is invalid") from exc

    return updated_at, conversation_id


@router.get(
    "/conversations",
    response_model=Result[ConversationListResponse],
    summary="List current user's conversations",
    description=(
        "用于左侧无限滚动会话列表，返回当前 TenJudge 用户自己的会话。"
        "结果按更新时间倒序排列。首次请求不传 cursor；"
        "当响应中的 next_cursor 不为 null 时，前端在下一页请求中原样作为 cursor 传回。"
        "cursor 是不透明字符串，前端不需要也不应该解析。"
    ),
)
async def list_conversations(
    token: Annotated[
        str | None,
        Header(alias="tenjudge-token", description="TenJudge 鉴权 token。"),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_CONVERSATION_PAGE_LIMIT,
            description="本次最多返回的会话数量。默认 20，最大 100。",
        ),
    ] = DEFAULT_CONVERSATION_PAGE_LIMIT,
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            description="上一页响应 next_cursor 返回的不透明游标。首次请求不传。",
        ),
    ] = None,
):
    if not token:
        raise BizException(Code.UNAUTHORIZED)

    # 鉴权
    user_id = await get_current_user_id(token)
    if user_id is None:
        raise BizException(Code.UNAUTHORIZED)

    before_updated_at, before_id = _decode_conversation_cursor(cursor)
    conversations = await ConversationRepository().list_by_user_id(
        user_id=user_id,
        limit=limit + 1,
        before_updated_at=before_updated_at,
        before_id=before_id,
    )

    visible_conversations = conversations[:limit]
    next_cursor = (
        _encode_conversation_cursor(visible_conversations[-1])
        if len(conversations) > limit and visible_conversations
        else None
    )

    return Result.success(
        ConversationListResponse(
            items=[
                ConversationListItem(
                    id=conversation.id,
                    title=conversation.title,
                )
                for conversation in visible_conversations
            ],
            next_cursor=next_cursor,
        )
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=Result[ConversationDetailResponse],
    summary="Get conversation detail",
    description=(
        "查询当前用户某个会话的详情和完整历史消息。"
        "如果会话状态为 running，响应会尽量返回当前轮的 running_task_id，"
        "前端可以使用该任务 ID 订阅 GET /chat/{task_id}/events 继续接收事件流。"
        "attachments 会按数据库中保存的内容原样返回。"
    ),
)
async def get_conversation_detail(
    conversation_id: Annotated[uuid.UUID, Path(description="要查询的会话 ID。")],
    token: Annotated[
        str | None,
        Header(alias="tenjudge-token", description="TenJudge 鉴权 token。"),
    ] = None,
):
    if not token:
        raise BizException(Code.UNAUTHORIZED)

    # 鉴权
    user_id = await get_current_user_id(token)
    if user_id is None:
        raise BizException(Code.UNAUTHORIZED)

    conversation_repository = ConversationRepository()
    message_repository = MessageRepository()
    task_repository = TaskRepository()

    conversation = await conversation_repository.get_by_id(conversation_id)
    if conversation is None:
        raise BizException(Code.CONVERSATION_NOT_FOUND)
    if conversation.user_id != user_id:
        raise BizException(Code.FORBIDDEN)

    messages = await message_repository.list_by_conversation(conversation.id)

    running_task_id = None
    if conversation.status == "running":
        task = await task_repository.get_by_key(conversation.id, conversation.current_turn)
        running_task_id = task.task_id if task is not None else None

    return Result.success(
        ConversationDetailResponse(
            id=conversation.id,
            title=conversation.title,
            status=conversation.status,
            running_task_id=running_task_id,
            messages=[
                ConversationMessageItem(
                    turn_index=message.turn_index,
                    role=message.role,
                    content=message.content,
                    attachments=message.attachments,
                )
                for message in messages
            ],
        )
    )
