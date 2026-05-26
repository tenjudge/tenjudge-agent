import asyncio
import httpx
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.context import Problem, Submission
from app.core.config import settings
from app.core.response import BizException, Code

JUDGE_PENDING_STATUS = "PENDING"
DEFAULT_JUDGE_TIMEOUT_SECONDS = 20.0
DEFAULT_JUDGE_POLL_INTERVAL_SECONDS = 1.0


class CurrentUserIdVO(BaseModel):
    user_id: int | None = Field(default=None, alias="userId")


class CurrentUserIdResult(BaseModel):
    code: int
    data: CurrentUserIdVO | None = None


class ProblemResult(BaseModel):
    code: int
    data: Problem | None = None


class SubmissionResult(BaseModel):
    code: int
    message: str | None = None
    data: Submission | None = None


class JudgeRequest(BaseModel):
    problem_id: int = Field(alias="problemId")
    language: Literal["cpp", "python"]
    code: str
    is_agent: bool = Field(default=True, alias="isAgent")

    model_config = ConfigDict(populate_by_name=True)


class SubmitJudgeVO(BaseModel):
    submission_id: int = Field(alias="submissionId")


class SubmitJudgeResult(BaseModel):
    code: int
    message: str | None = None
    data: SubmitJudgeVO | None = None


class JudgeWaitResult(BaseModel):
    success: bool
    submission: Submission | None = None
    message: str = ""


def get_tenjudge_server_base_url() -> str:
    base_url = settings.TENJUDGE_SERVER_BASE_URL.rstrip("/")
    if not base_url:
        raise ValueError("TENJUDGE_SERVER_BASE_URL is empty")
    return base_url


async def get_current_user_id(token: str) -> int | None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            f"{get_tenjudge_server_base_url()}/auth/me/id",
            headers={"tenjudge-token": token},
        )
        response.raise_for_status()

    result = CurrentUserIdResult.model_validate(response.json())

    if result.code != Code.SUCCESS.biz_code:
        raise BizException(Code.UNAUTHORIZED)

    return result.data.user_id if result.data else None


async def get_problem(problem_id: int, token: str) -> Problem:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            f"{get_tenjudge_server_base_url()}/agent/problem/{problem_id}",
            headers={"tenjudge-token": token},
        )
        response.raise_for_status()

    result = ProblemResult.model_validate(response.json())

    if result.code != Code.SUCCESS.biz_code:
        raise BizException(Code.PARAM_ERROR, "problem is invalid")
    if result.data is None:
        raise BizException(Code.SERVER_ERROR, "problem data is empty")

    return result.data


async def get_submission(submission_id: int, token: str) -> Submission:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            f"{get_tenjudge_server_base_url()}/submit/{submission_id}",
            headers={"tenjudge-token": token},
        )
        response.raise_for_status()

    result = SubmissionResult.model_validate(response.json())

    if result.code != Code.SUCCESS.biz_code:
        raise BizException(Code.PARAM_ERROR, result.message or "submission is invalid")
    if result.data is None:
        raise BizException(Code.SERVER_ERROR, "submission data is empty")

    return result.data


async def submit_judge(
        problem_id: int,
        language: Literal["cpp", "python"],
        code: str,
        token: str,
) -> int:
    judge_request = JudgeRequest(
        problem_id=problem_id,
        language=language,
        code=code,
        is_agent=True,
    )
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(
            f"{get_tenjudge_server_base_url()}/submit/judge",
            headers={"tenjudge-token": token},
            json=judge_request.model_dump(mode="json", by_alias=True),
        )
        response.raise_for_status()

    result = SubmitJudgeResult.model_validate(response.json())

    if result.code != Code.SUCCESS.biz_code:
        raise BizException(Code.PARAM_ERROR, result.message or "judge submission is invalid")
    if result.data is None:
        raise BizException(Code.SERVER_ERROR, "judge submission data is empty")

    return result.data.submission_id


async def submit_judge_and_wait(
        problem_id: int,
        language: Literal["cpp", "python"],
        code: str,
        token: str,
        timeout_seconds: float = DEFAULT_JUDGE_TIMEOUT_SECONDS,
        poll_interval_seconds: float = DEFAULT_JUDGE_POLL_INTERVAL_SECONDS,
) -> JudgeWaitResult:
    submission_id = await submit_judge(
        problem_id=problem_id,
        language=language,
        code=code,
        token=token,
    )
    deadline = time.monotonic() + timeout_seconds

    while True:
        # 1. 提交成功后查询测评详情；业务层查不到时返回失败结果，供 tool 直接反馈。
        try:
            submission = await get_submission(submission_id, token)
        except BizException as exc:
            return JudgeWaitResult(
                success=False,
                submission=None,
                message=f"submission {submission_id} is unavailable: {exc.message}",
            )

        # 2. TenJudge 当前只有 PENDING 表示仍在测评，其它状态都视为已有最终结果。
        if submission.status != JUDGE_PENDING_STATUS:
            return JudgeWaitResult(success=True, submission=submission)

        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return JudgeWaitResult(
                success=False,
                submission=submission,
                message=f"judge result is still pending after {timeout_seconds:g} seconds",
            )

        await asyncio.sleep(min(poll_interval_seconds, remaining_seconds))
