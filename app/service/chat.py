import uuid
import asyncio
import json
from typing import Any

from datetime import datetime

from langchain_core.messages import HumanMessage

from app.agents.context import (
    CodeFile,
    CodeFileContext,
    Problem,
    ProblemContext,
    Submission,
    SubmissionContext,
)
from app.agents.orchestrator import (
    State,
    get_init_state,
    next_code_file_context_id,
    next_problem_context_id,
    next_submission_context_id,
    state_from_dict,
    state_to_dict,
)
from app.core.db import pool
from app.core.response import BizException, Code
from app.repository.conversations import Conversation, ConversationRepository
from app.repository.messages import Message, MessageRepository
from app.repository.states import StateRepository
from app.repository.tasks import Task, TaskRepository
from app.service.runner import run_task
from app.service.tenjudge_server import get_problem, get_submission


def _dump_problem_for_message(problem: Problem) -> str:
    # 拼接给模型看的题面时排除题解，避免题解误导正常解题过程。
    payload = problem.model_dump(mode="json", by_alias=True, exclude={"solution"})
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _dump_submission_for_message(submission: Submission) -> str:
    # 提交代码会单独放入 code_files，这里避免在消息里重复一份大代码。
    payload = submission.model_dump(mode="json", by_alias=True, exclude={"code"})
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _print_state(state: State) -> None:
    # 调试展示 state：转成可序列化字典后再格式化输出，方便人工查看完整结构。
    print(json.dumps(state_to_dict(state), ensure_ascii=False, indent=2, default=str))


def _append_problem_context(state: State, problem: Problem) -> ProblemContext:
    # 每次附件都生成新的上下文 id，不按平台 problem id 去重。
    problem_context = ProblemContext(
        id=next_problem_context_id(state),
        problem=problem,
    )
    state["problems"].append(problem_context)
    return problem_context


def _append_submission_context(state: State, submission: Submission) -> SubmissionContext:
    submission_context = SubmissionContext(
        id=next_submission_context_id(state),
        submission=submission,
    )
    state["submissions"].append(submission_context)
    return submission_context


def _append_submission_code_file_context(
        state: State,
        submission_context: SubmissionContext,
        problem_context: ProblemContext,
) -> CodeFileContext:
    # 1. 从提交上下文和题目上下文中取出描述代码来源所需的信息
    submission = submission_context.submission
    problem = problem_context.problem

    # 2. 将提交源码单独保存为 code_file_N，方便后续 agent 按代码文件引用
    code_file_context = CodeFileContext(
        id=next_code_file_context_id(state),
        file=CodeFile(
            description=(
                f"Source code extracted from submission {submission_context.id} "
                f"for problem {problem_context.id}.\n"
                f"Submission id: {submission.submission_id}.\n"
                f"Problem id: {submission.problem_id}.\n"
                f"Problem name: {problem.name}.\n"
                f"Submission status: {submission.status}.\n"
                f"Use this file as the code reference when reasoning about {submission_context.id}."
            ),
            language=submission.language if submission.language in {"cpp", "python"} else "else",
            content=submission.code,
        ),
    )

    # 3. 写入 state["code_files"]，保持和 problem/submission 上下文一致
    state["code_files"].append(code_file_context)
    return code_file_context

# 题目 -> HumanMessage，消息中的题面 JSON 不包含 solution 字段，避免误导模型直接看题解。
def _build_problem_attachment_message(problem_context: ProblemContext) -> HumanMessage:
    content = (
        "[Attachment: problem]\n\n"
        "The user attached a programming problem.\n\n"
        f"Problem context id: {problem_context.id}\n\n"
        "Use this problem as authoritative context when answering the user's request.\n"
        f"If referring to this problem later, use {problem_context.id}.\n\n"
        "Problem JSON:\n"
        f"{_dump_problem_for_message(problem_context.problem)}"
    )
    return HumanMessage(content=content)

# 提交 -> HumanMessage，包含提交信息+提交代码+题目信息（不包含题解）。
def _build_submission_attachment_message(
        submission_context: SubmissionContext,
        problem_context: ProblemContext,
        code_file_context: CodeFileContext,
) -> HumanMessage:
    content = (
        "[Attachment: submission]\n\n"
        "The user attached a programming submission.\n\n"
        f"Submission context id: {submission_context.id}\n"
        f"Related problem context id: {problem_context.id}\n"
        f"Submitted code file id: {code_file_context.id}\n\n"
        f"The submitted source code has been stored separately as {code_file_context.id}.\n"
        f"Use {code_file_context.id} as the code reference when reasoning about {submission_context.id}.\n\n"
        "In the submission JSON, time values are measured in ms and memory values are measured in MB.\n\n"
        "Submission JSON:\n"
        f"{_dump_submission_for_message(submission_context.submission)}\n\n"
        f"Submitted Code ({code_file_context.id}):\n"
        "```\n"
        f"{code_file_context.file.content}\n"
        "```\n\n"
        "Problem JSON:\n"
        f"{_dump_problem_for_message(problem_context.problem)}"
    )
    return HumanMessage(content=content)


async def handle_chat(request: Any, token: str, user_id: int) -> dict[str, uuid.UUID]:

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
                conversation = await conversation_repository.insert(conversation, conn=conn)

                # 2. 更新当前轮数
                current_turn_index = 1

            # 获取当前轮的输入 state
            if current_turn_index == 1:
                current_state = get_init_state()
            else:
                previous_task = await task_repository.get_by_key(conversation.id, current_turn_index - 1, conn=conn)
                if previous_task is None or previous_task.state is None:
                    raise BizException(Code.SERVER_ERROR, "previous task state not found")

                previous_state_record = await state_repository.select(previous_task.state, conn=conn)
                if previous_state_record is None:
                    raise BizException(Code.SERVER_ERROR, "previous state not found")

                current_state = state_from_dict(previous_state_record.state)
            current_state["token"] = token
            current_state["user_id"] = user_id

            # 1. 处理用户附件：代码源码留给 runner，题目/提交先转换成结构化 state 上下文
            code_sources: list[str] = []
            attachment_messages: list[HumanMessage] = []
            for attachment in request.attachments:
                if attachment.type == "code":
                    code_sources.append(attachment.content)
                elif attachment.type == "problem":
                    # 1.1 题目附件：查询题面并写入 state["problems"]
                    problem = await get_problem(attachment.problem_id, token)
                    problem_context = _append_problem_context(current_state, problem)

                    # 1.2 根据题目上下文生成 HumanMessage，消息中的题面 JSON 不包含 solution
                    attachment_messages.append(_build_problem_attachment_message(problem_context))
                elif attachment.type == "submission":
                    # 1.1 提交附件：先查询提交，再查询提交所属题面
                    submission = await get_submission(attachment.submission_id, token)
                    problem = await get_problem(submission.problem_id, token)

                    # 1.2 将题面和提交分别写入 state，context id 使用各自 cnt 生成
                    problem_context = _append_problem_context(current_state, problem)
                    submission_context = _append_submission_context(current_state, submission)

                    # 1.3 将提交代码拆成单独的 code_file_N，避免只藏在 submission.code 中
                    code_file_context = _append_submission_code_file_context(
                        current_state,
                        submission_context,
                        problem_context,
                    )

                    # 1.4 根据提交、题面、代码文件三者的关系生成 HumanMessage
                    attachment_messages.append(_build_submission_attachment_message(
                        submission_context,
                        problem_context,
                        code_file_context,
                    ))

            # 2. 附件上下文先进入 state，后续 run_task 再追加用户本轮自然语言消息
            current_state["messages"].extend(attachment_messages)

            # 3. 更新messages表中用户消息
            await message_repository.insert(Message(
                conversation_id=conversation.id,
                turn_index=current_turn_index,
                role="user",
                content=request.message,
                attachments=[attachment.model_dump() for attachment in request.attachments],
            ), conn=conn)

            # 4. 更新task表（state留空）
            current_task_id = uuid.uuid4()
            await task_repository.insert(Task(
                conversation_id=conversation.id,
                turn_index=current_turn_index,
                task_id=current_task_id,
                state=None,
            ), conn=conn)
    # _print_state(current_state)

    asyncio.create_task(run_task(
        task_id=current_task_id,
        message=request.message,
        code_sources=code_sources,
        current_state=current_state,
    ))

    return {
        "conversation_id": conversation.id,
        "task_id": current_task_id,
    }
