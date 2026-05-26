import json
from typing import Literal, cast

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.agents.context import Submission, find_code_file_context, format_available_code_file_ids
from app.service.tenjudge_server import submit_judge_and_wait


SUPPORTED_JUDGE_LANGUAGES = {"cpp", "python"}


class SubmitCodeForJudgeInput(BaseModel):
    code_file_id: str = Field(
        min_length=1,
        description="The existing code file context id to submit, such as code_file_1.",
    )
    problem_id: int = Field(
        gt=0,
        description="The TenJudge problem id to judge this code against.",
    )


def _submission_without_code(submission: Submission | None) -> dict | None:
    if submission is None:
        return None
    return submission.model_dump(mode="json", by_alias=True, exclude={"code"})


def _format_result(
        *,
        success: bool,
        message: str,
        code_file_id: str,
        problem_id: int,
        language: str | None,
        submission: Submission | None,
) -> str:
    return json.dumps({
        "success": success,
        "message": message,
        "code_file_id": code_file_id,
        "problem_id": problem_id,
        "language": language,
        "submission": _submission_without_code(submission),
    }, ensure_ascii=False, default=str)


TOOL_DESCRIPTION = """Submit one existing code file from the current agent state to the TenJudge judge system.
Use this tool when a state code file should be evaluated against a TenJudge problem, including checking user-provided code or validating code produced or revised during the conversation.
The input code_file_id must be one of the code_file_N identifiers already present in the conversation/state.
The problem_id is the TenJudge problem id to judge against; it does not need to be a problem already attached in state.
Do not copy source code into this tool. The tool reads the source code and language from state by code_file_id.
The language is inferred from the selected code file and must be cpp or python.
The return value is JSON containing whether a final judge result was obtained, a message, the selected code file id, problem id, language, and the submission detail when available. The submission code is omitted to save context."""


@tool(
    args_schema=SubmitCodeForJudgeInput,
    description=TOOL_DESCRIPTION,
)
async def submit_code_for_judge(
        code_file_id: str,
        problem_id: int,
        state: Annotated[dict, InjectedState],
) -> str:
    """Submit a state code file to TenJudge and wait briefly for the judge result."""
    code_file_context = find_code_file_context(state, code_file_id)
    if code_file_context is None:
        return _format_result(
            success=False,
            message=(
                f"code_file_id {code_file_id} was not found. "
                f"Available code files: {format_available_code_file_ids(state)}"
            ),
            code_file_id=code_file_id,
            problem_id=problem_id,
            language=None,
            submission=None,
        )

    code_file = code_file_context.file
    if code_file.language not in SUPPORTED_JUDGE_LANGUAGES:
        return _format_result(
            success=False,
            message=(
                f"{code_file_id} uses unsupported language {code_file.language}. "
                "TenJudge judge supports cpp and python."
            ),
            code_file_id=code_file_id,
            problem_id=problem_id,
            language=code_file.language,
            submission=None,
        )

    token = state.get("token", "")
    if not token:
        return _format_result(
            success=False,
            message="authenticated TenJudge token is missing from agent state",
            code_file_id=code_file_id,
            problem_id=problem_id,
            language=code_file.language,
            submission=None,
        )

    judge_result = await submit_judge_and_wait(
        problem_id=problem_id,
        language=cast(Literal["cpp", "python"], code_file.language),
        code=code_file.content,
        token=token,
    )
    return _format_result(
        success=judge_result.success,
        message=judge_result.message,
        code_file_id=code_file_id,
        problem_id=problem_id,
        language=code_file.language,
        submission=judge_result.submission,
    )
