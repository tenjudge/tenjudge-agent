import uuid
import logging
import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.code_summarize_agent import summarize_code_files
from app.agents.context import CodeFileContext
from app.agents.orchestrator import AGENT_TOOLS, State, agent, next_code_file_context_id, state_to_dict
from app.agents.plan_agent import format_plan_for_message, make_plan
from app.agents.title_agent import summarize_title
from app.core.config import settings
from app.core.db import pool
from app.core.redis import redis_client
from app.repository.conversations import ConversationRepository
from app.repository.messages import Message, MessageRepository
from app.repository.states import State as StateRecord, StateRepository
from app.repository.tasks import TaskRepository


logger = logging.getLogger(__name__)
FAILED_MESSAGE = "Sorry, the agent task failed."


def _task_stream_key(task_id: uuid.UUID) -> str:
    return f"agent:task:{task_id}:events"


async def _publish_task_event(task_id: uuid.UUID, event: str, data: str):
    stream_key = _task_stream_key(task_id)
    await redis_client.xadd(stream_key, {
        "event": event,
        "data": data,
    })
    await redis_client.expire(stream_key, settings.REDIS_STREAM_TTL_SECONDS)


async def _run_title_task(
        conversation_id: uuid.UUID,
        turn_index: int,
        task_id: uuid.UUID,
        message: str,
):
    try:
        # 1. title 只基于 run_task 收到的本轮自然语言消息生成，不读取附件和 state。
        title = await summarize_title(message)

        # 2. 用 task_id 做条件更新，避免第 1 轮重开时旧后台任务覆盖新标题。
        conversation_repository = ConversationRepository()
        conversation = await conversation_repository.update_title_by_task(
            conversation_id=conversation_id,
            turn_index=turn_index,
            task_id=task_id,
            title=title,
        )
        if conversation is None:
            logger.info(
                "Skip stale conversation title task: conversation_id=%s turn_index=%s task_id=%s",
                conversation_id,
                turn_index,
                task_id,
            )
            return

        await _publish_task_event(task_id, "title", title)
    except Exception:
        logger.exception("Conversation title task failed: task_id=%s", task_id)


def _build_code_attachment_message(code_file_context: CodeFileContext) -> HumanMessage:
    code_file = code_file_context.file
    code_block_language = "" if code_file.language == "else" else code_file.language

    # 1. 代码附件消息里同时带上 description 和完整源码，方便后续模型直接读上下文。
    content = (
        "[Attachment: code]\n\n"
        "The user attached a source code file.\n\n"
        f"Code file context id: {code_file_context.id}\n"
        f"Language: {code_file.language}\n"
        f"Description: {code_file.description}\n\n"
        f"The complete source code is stored as {code_file_context.id}.\n"
        f"Use {code_file_context.id} as the code reference when reasoning about this source file.\n\n"
        f"Source Code ({code_file_context.id}):\n"
        f"```{code_block_language}\n"
        f"{code_file.content}\n"
        "```"
    )
    return HumanMessage(content=content)


async def run_task(
        conversation_id: uuid.UUID, # 会话编号，用于写回消息和状态
        turn_index: int, # 当前轮次，用于写回 agent message
        task_id: uuid.UUID, # 任务编号，用于拼接 Redis Stream 的 key
        message: str, # 用户提问的消息
        code_sources: list[str],  # 用户上传的代码源码
        current_state: State, # 当前轮输入 state
):
    user_message_appended = False
    task_saved = False

    # 1. 首轮标题生成完全后台执行，不阻塞主 agent 流程，也不影响 done 的发送时机。
    if turn_index == 1:
        asyncio.create_task(_run_title_task(
            conversation_id=conversation_id,
            turn_index=turn_index,
            task_id=task_id,
            message=message,
        ))

    try:
        # 1. 先处理用户上传的代码源码，生成 CodeFile 并追加到 current_state["code_files"]。
        history_messages = list(current_state["messages"])  # 当前轮自然语言消息还没进入 state，单独传给 summarizer。
        code_files = await summarize_code_files(
            code_sources=code_sources,
            message=message,
            history_messages=history_messages,
        )

        # 2. 每份代码都分配稳定的 code_file_N，同时写入 state 和 messages。
        for code_file in code_files:
            code_file_context = CodeFileContext(
                id=next_code_file_context_id(current_state),  # 这里会同步推进 code_file_cnt。
                file=code_file,
            )
            current_state["code_files"].append(code_file_context)
            current_state["messages"].append(_build_code_attachment_message(code_file_context))

        # 3. 再追加本轮用户自然语言消息，保证 agent 看到的上下文顺序稳定。
        current_state["messages"].append(HumanMessage(content=message))
        user_message_appended = True

        # 4. 基于当前完整上下文和主 agent 工具列表生成内部计划，并写入长期 messages。
        await _publish_task_event(task_id, "progress", "Planning response")
        plan = await make_plan(
            messages=current_state["messages"],
            available_tools=AGENT_TOOLS,
        )
        current_state["messages"].append(SystemMessage(content=format_plan_for_message(plan)))

        # 5. 流式执行主 agent：custom 转 progress，agent_node 的 messages 转回答流。
        await _publish_task_event(task_id, "progress", "Thinking")
        final_answer = ""
        final_state = current_state
        async for stream_mode, chunk in agent.astream(
            current_state,
            stream_mode=["messages", "custom", "values"],
        ):
            if stream_mode == "custom":
                await _publish_task_event(task_id, "progress", str(chunk))
            elif stream_mode == "messages":
                message_chunk, metadata = chunk
                if metadata.get("langgraph_node") == "agent_node":
                    content = getattr(message_chunk, "content", "")
                    if content:
                        content = content if isinstance(content, str) else str(content)
                        final_answer += content
                        await _publish_task_event(task_id, "message", content)
            elif stream_mode == "values":
                final_state = chunk

        if not final_answer:
            for state_message in reversed(final_state["messages"]):
                if isinstance(state_message, AIMessage):
                    final_answer = str(state_message.content)
                    break

        # 6. 正常完成后落库最终 state、agent 消息和会话状态。
        state_id = uuid.uuid7()
        state_repository = StateRepository()
        task_repository = TaskRepository()
        message_repository = MessageRepository()
        conversation_repository = ConversationRepository()
        async with pool.connection() as conn:
            async with conn.transaction():
                await state_repository.insert(StateRecord(
                    id=state_id,
                    state=state_to_dict(final_state),
                ), conn=conn)
                await task_repository.update_state_by_task_id(task_id, state_id, conn=conn)
                await message_repository.insert(Message(
                    conversation_id=conversation_id,
                    turn_index=turn_index,
                    role="agent",
                    content=final_answer,
                ), conn=conn)
                await conversation_repository.update_status(conversation_id, "finished", conn=conn)

        task_saved = True
        await _publish_task_event(task_id, "done", "")
    except Exception:
        if task_saved:
            logger.exception("Failed to publish completed agent task event: task_id=%s", task_id)
            return
        logger.exception("Agent task failed: task_id=%s", task_id)

        # 1. 失败也保存当前轮 state，避免下一轮加载上一轮状态时断掉。
        if not user_message_appended:
            current_state["messages"].append(HumanMessage(content=message))
        current_state["messages"].append(AIMessage(content=FAILED_MESSAGE))

        state_id = uuid.uuid7()
        state_repository = StateRepository()
        task_repository = TaskRepository()
        message_repository = MessageRepository()
        conversation_repository = ConversationRepository()
        try:
            async with pool.connection() as conn:
                async with conn.transaction():
                    await state_repository.insert(StateRecord(
                        id=state_id,
                        state=state_to_dict(current_state),
                    ), conn=conn)
                    await task_repository.update_state_by_task_id(task_id, state_id, conn=conn)
                    await message_repository.insert(Message(
                        conversation_id=conversation_id,
                        turn_index=turn_index,
                        role="agent",
                        content=FAILED_MESSAGE,
                    ), conn=conn)
                    await conversation_repository.update_status(conversation_id, "finished", conn=conn)
        except Exception:
            logger.exception("Failed to persist failed agent task: task_id=%s", task_id)

        # 2. 通知 SSE 订阅方任务失败并结束事件流。
        try:
            await _publish_task_event(task_id, "failed", FAILED_MESSAGE)
            await _publish_task_event(task_id, "done", "")
        except Exception:
            logger.exception("Failed to publish failed agent task event: task_id=%s", task_id)
