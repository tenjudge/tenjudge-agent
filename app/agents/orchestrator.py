import operator
from typing import Any

from langchain.messages import AnyMessage
from langchain_core.messages import messages_from_dict, messages_to_dict
from typing_extensions import TypedDict, Annotated

from app.agents.common import CodeFileContext, ProblemContext, SubmissionContext


class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    code_files: list[CodeFileContext]
    problems: list[ProblemContext]
    submissions: list[SubmissionContext]
    code_file_cnt: int
    problem_cnt: int
    submission_cnt: int
    token: str
    user_id: int


def get_init_state() -> State:
    return {
        "messages": [],
        "code_files": [],
        "problems": [],
        "submissions": [],
        "code_file_cnt": 0,
        "problem_cnt": 0,
        "submission_cnt": 0,
        "token": "",
        "user_id": 0,
    }


def state_to_dict(state: State) -> dict[str, Any]:
    return {
        "messages": messages_to_dict(state["messages"]),
        "code_files": [file.model_dump() for file in state["code_files"]],
        "problems": [problem.model_dump() for problem in state["problems"]],
        "submissions": [submission.model_dump() for submission in state["submissions"]],
        "code_file_cnt": state["code_file_cnt"],
        "problem_cnt": state["problem_cnt"],
        "submission_cnt": state["submission_cnt"],
        "token": state["token"],
        "user_id": state["user_id"],
    }


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
