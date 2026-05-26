import operator
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.messages import AnyMessage
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, messages_from_dict, messages_to_dict
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict, Annotated

from app.agents.context import CodeFileContext, ProblemContext, SubmissionContext
from app.agents.models import LLM
from app.core.config import settings
from app.tools.database import query_oj_database
from app.tools.judge import submit_code_for_judge
from app.tools.misc import get_current_time, get_current_user_id


logger = logging.getLogger(__name__)

##### State 定义与相关函数 ##############################################

INITIAL_SYSTEM_PROMPT = "You are the TenJudge online judge platform assistant."


# LangGraph 持久化和节点共享的状态结构。
class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    code_files: list[CodeFileContext]
    problems: list[ProblemContext]
    submissions: list[SubmissionContext]
    code_file_cnt: int  # 用于生成 code_file_N
    problem_cnt: int  # 用于生成 problem_N
    submission_cnt: int  # 用于生成 submission_N
    token: str
    user_id: int


# 创建一份新的空 Agent state，供新会话或首轮任务使用。
def get_init_state() -> State:
    return {
        "messages": [SystemMessage(content=INITIAL_SYSTEM_PROMPT)],
        "code_files": [],
        "problems": [],
        "submissions": [],
        "code_file_cnt": 0,
        "problem_cnt": 0,
        "submission_cnt": 0,
        "token": "",
        "user_id": 0,
    }


# 推进代码文件计数器，并返回新的 agent-facing code file context id。
def next_code_file_context_id(state: State) -> str:
    state["code_file_cnt"] += 1
    return f"code_file_{state['code_file_cnt']}"


# 推进题目计数器，并返回新的 agent-facing problem context id。
def next_problem_context_id(state: State) -> str:
    state["problem_cnt"] += 1
    return f"problem_{state['problem_cnt']}"


# 推进提交计数器，并返回新的 agent-facing submission context id。
def next_submission_context_id(state: State) -> str:
    state["submission_cnt"] += 1
    return f"submission_{state['submission_cnt']}"


# 将 LangGraph state 转成可写入 JSONB 的普通字典。
def state_to_dict(state: State) -> dict[str, Any]:
    return {
        "messages": messages_to_dict(state["messages"]),
        "code_files": [file.model_dump(mode="json") for file in state["code_files"]],
        "problems": [problem.model_dump(mode="json") for problem in state["problems"]],
        "submissions": [submission.model_dump(mode="json") for submission in state["submissions"]],
        "code_file_cnt": state["code_file_cnt"],
        "problem_cnt": state["problem_cnt"],
        "submission_cnt": state["submission_cnt"],
        "token": state["token"],
        "user_id": state["user_id"],
    }


# 将数据库中的 state 字典恢复成 LangGraph 可执行的 State 对象。
def state_from_dict(state: dict[str, Any]) -> State:
    code_files = state.get("code_files", state.get("files", []))
    return {
        "messages": messages_from_dict(state["messages"]),
        "code_files": [CodeFileContext.model_validate(file) for file in code_files],
        "problems": [ProblemContext.model_validate(problem) for problem in state.get("problems", [])],
        "submissions": [
            SubmissionContext.model_validate(submission)
            for submission in state.get("submissions", [])
        ],
        "code_file_cnt": state["code_file_cnt"],
        "problem_cnt": state["problem_cnt"],
        "submission_cnt": state["submission_cnt"],
        "token": state["token"],
        "user_id": state["user_id"],
    }


##### LangGraph 节点定义 ##############################################

# 主 agent 可使用的业务工具列表，planner 和 graph 后续应共用这个入口。
# TODO 接入题目、提交、代码文件等更多业务工具后，在这里统一维护。
AGENT_TOOLS: list[BaseTool] = [
    query_oj_database,
    submit_code_for_judge,
    get_current_time,
    get_current_user_id,
]

TOOL_PROGRESS_MESSAGES = {
    "query_oj_database": "Querying database",
    "submit_code_for_judge": "Submitting code for judging",
    "get_current_time": "Checking current time",
    "get_current_user_id": "Checking current user",
}


def _write_progress_event(message: str) -> None:
    try:
        get_stream_writer()(message)
    except RuntimeError:
        logger.debug("Skip progress event outside stream context: %s", message)


def _get_tool_call_name(request: Any) -> str | None:
    tool = getattr(request, "tool", None)
    tool_name = getattr(tool, "name", None)
    if tool_name:
        return tool_name

    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        return tool_call.get("name")
    return getattr(tool_call, "name", None)


def _get_model_tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "unknown")
    return str(getattr(tool_call, "name", None) or "unknown")


async def _wrap_tool_call_with_progress(
        request: Any,
        call_tool: Callable[[Any], Awaitable[Any]],
) -> Any:
    tool_name = _get_tool_call_name(request)
    _write_progress_event(TOOL_PROGRESS_MESSAGES.get(tool_name, "Using tool"))
    return await call_tool(request)


# 从最后一条消息向前扫描到最近的 HumanMessage，统计本轮已经发生的模型推理轮数。
def _current_turn_react_round_count(state: State) -> int:
    count = 0
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage):
            count += 1
    return count


# [node] ReAct 推理节点；达到轮数上限后不再绑定工具，让模型直接输出内容。
async def agent_node(state: State) -> dict[str, list[AnyMessage]]:
    tools = [] if _current_turn_react_round_count(state) >= settings.AGENT_MAX_REACT_ROUNDS else AGENT_TOOLS
    response = await LLM("medium").ainvoke(
        messages=state["messages"],
        tools=tools,
    )
    content = getattr(response, "content", "")
    if content:
        logger.info("【模型输出】\n%s", content if isinstance(content, str) else str(content))

    for tool_call in getattr(response, "tool_calls", None) or []:
        logger.info("【工具调用】%s", _get_model_tool_call_name(tool_call))

    return {"messages": [response]}


# [router] 根据 agent_node 的最后一条消息决定进入工具节点还是结束。
def route_after_agent(state: State) -> str:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None) or []

    if tool_calls:
        return "tools"

    return "end"


##### LangGraph 编排 ##############################################

workflow = StateGraph(State)
workflow.add_node("agent_node", agent_node)
workflow.add_node("tools_node", ToolNode(
    AGENT_TOOLS,
    awrap_tool_call=_wrap_tool_call_with_progress,
))

workflow.add_edge(START, "agent_node")
workflow.add_conditional_edges(
    "agent_node",
    route_after_agent,
    {
        "tools": "tools_node",
        "end": END,
    },
)
workflow.add_edge("tools_node", "agent_node")

# 编译后的 LangGraph agent，runner 后续可直接导入并执行。
agent = workflow.compile()
