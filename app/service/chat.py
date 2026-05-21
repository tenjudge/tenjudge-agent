import uuid
import operator

from pydantic import BaseModel, Field, StringConstraints

from langchain.messages import AnyMessage
from typing_extensions import TypedDict, Annotated
from typing import List, Literal, Union
from datetime import datetime

from app.agents.orchestrator import get_init_state, state_from_dict
from app.core.db import pool
from app.core.response import BizException, Code
from app.repository.conversations import Conversation, ConversationRepository
from app.repository.messages import Message, MessageRepository
from app.repository.states import StateRepository
from app.repository.tasks import Task, TaskRepository


# ===== Request ==========================================================
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

# ===== State ==========================================================

class CodeFile(BaseModel):
    id: int
    description: str
    language: Literal["cpp", "python", "else"]
    content: str

class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    files: List[CodeFile]
    token: str
    user_id: int

async def handle_chat(request: ChatRequest, token: str, user_id: int):

    # TODO 加分布式锁
    conversation_repository = ConversationRepository()
    message_repository = MessageRepository()
    state_repository = StateRepository()
    task_repository = TaskRepository()

    async with pool.connection() as conn:
        async with conn.transaction():
            if request.conversation_id:

                # 1. 获取旧会话数据并校验参数
                conversation = await conversation_repository.get_by_id(request.conversation_id, conn=conn)
                if conversation is None: # 检查会话是否存在
                    raise BizException(Code.CONVERSATION_NOT_FOUND)
                if conversation.user_id != user_id: # 检查会话是否属于当前用户
                    raise BizException(Code.FORBIDDEN)
                if conversation.status == "running": # 检查会话是否正在运行
                    raise BizException(Code.CONVERSATION_IS_RUNNING)
                if request.turn_index is not None and (request.turn_index < 1 or request.turn_index > conversation.current_turn): # 检查对话轮数参数是否合法
                    raise BizException(Code.PARAM_ERROR, "turn_index is invalid")

                # 2. 更新会话数据（状态，轮数）
                await conversation_repository.update_status(conversation.id, "running", conn=conn)
                current_turn_index = request.turn_index if request.turn_index is not None else conversation.current_turn + 1
                await conversation_repository.update_current_turn(conversation.id, current_turn_index, conn=conn)

                # 3. 删除当前轮及之后的 messages、tasks 和 task 独占的 states
                deleted_state_ids = await task_repository.delete_from_turn(conversation.id, current_turn_index, conn=conn)
                await state_repository.delete_by_ids(deleted_state_ids, conn=conn)
                await message_repository.delete_from_turn(conversation.id, current_turn_index, conn=conn)

            else:
                # 1. 新建 conversation
                conversation = Conversation(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    title=None,
                    updated_at=datetime.now(),
                    current_turn=1,
                    status="running",
                )
                await conversation_repository.insert(conversation, conn=conn)

                # 2. 更新当前轮数
                current_turn_index = 1

            # 获取当前轮的输入 state
            if current_turn_index == 1:
                previous_state = get_init_state()
            else:
                previous_task = await task_repository.get_by_key(conversation.id, current_turn_index - 1, conn=conn)
                if previous_task is None or previous_task.state is None:
                    raise BizException(Code.SERVER_ERROR, "previous task state not found")

                previous_state_record = await state_repository.select(previous_task.state, conn=conn)
                if previous_state_record is None:
                    raise BizException(Code.SERVER_ERROR, "previous state not found")

                previous_state = state_from_dict(previous_state_record.state)

            # 更新messages表中用户消息
            await message_repository.insert(Message(
                conversation_id=conversation.id,
                turn_index=current_turn_index,
                role="user",
                content=request.message,
                attachments=[attachment.model_dump() for attachment in request.attachments],
            ), conn=conn)

            # 更新task表（state留空）
            current_task_id = uuid.uuid4()
            await task_repository.insert(Task(
                conversation_id=conversation.id,
                turn_index=current_turn_index,
                task_id=current_task_id,
                state=None,
            ), conn=conn)

    # TODO 生成当前的 state

    for attachment in request.attachments:
        if attachment.type == "code":
            print("用户上传代码")
            print(attachment.content)

        elif attachment.type == "submission":
            print("用户选择提交")
            print(attachment.submission_id)

        elif attachment.type == "problem":
            print("用户选择题目")
            print(attachment.problem_id)
