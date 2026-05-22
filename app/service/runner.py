import uuid

from langchain_core.messages import HumanMessage

from app.agents.code_summarize_agent import summarize_code_files
from app.agents.context import CodeFileContext
from app.agents.orchestrator import State, next_code_file_context_id


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
        task_id: uuid.UUID, # 任务编号，用于拼接 Redis Stream 的 key
        message: str, # 用户提问的消息
        code_sources: list[str],  # 用户上传的代码源码
        current_state: State, # 当前轮输入 state
):
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

        # 4. 执行主 agent，并在正常完成后落库最终 state、agent 消息和会话状态。
        # TODO 调用 LangGraph/agent 主流程，写入 tasks.state、messages.agent，并更新 conversation.status。
        pass
    except Exception as exc:
        # 1. 异常时需要把任务失败结果写回数据库，避免 conversation 一直停在 running。
        # TODO 根据 task_id 定位任务和会话，写入失败状态/错误信息，并更新 conversation.status。

        # 2. 异常时需要通过 Redis Stream 写入失败事件，让 SSE 订阅方能收到失败结果。
        # TODO 向 task_id 对应的 Redis Stream 推送 error/done 事件。

        # 3. 后续接入日志系统时，在这里记录完整异常，方便排查异步任务失败原因。
        # TODO 记录 exc 和 traceback。
        pass
