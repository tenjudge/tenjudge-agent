import uuid

from app.agents.orchestrator import State

async def run_task(
        task_id: uuid.UUID, # 任务编号，用于拼接 Redis Stream 的 key
        message: str, # 用户提问的消息
        code_sources: list[str],  # 用户上传的代码源码
        current_state: State, # 当前轮输入 state
):
    # TODO 调用大语言模型处理 code_sources，并追加到 current_state["messages"]。
    # TODO 将本轮用户消息追加到 current_state["messages"]。
    pass
