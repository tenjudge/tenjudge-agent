import httpx
from pydantic import BaseModel, Field, ValidationError

from app.agents.context import Problem, Submission
from app.core.config import settings
from app.core.response import BizException, Code


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
    data: Submission | None = None


def get_tenjudge_server_base_url() -> str:
    base_url = settings.TENJUDGE_SERVER_BASE_URL.rstrip("/")
    if not base_url:
        raise BizException(Code.SERVER_ERROR)
    return base_url


async def get_current_user_id(token: str) -> int | None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{get_tenjudge_server_base_url()}/auth/me/id",
                headers={"tenjudge-token": token},
            )
            response.raise_for_status()

        result = CurrentUserIdResult.model_validate(response.json())
    except httpx.HTTPError as exc:
        raise BizException(Code.SERVER_ERROR) from exc
    except (ValueError, ValidationError) as exc:
        raise BizException(Code.SERVER_ERROR) from exc

    if result.code != Code.SUCCESS.biz_code:
        raise BizException(Code.UNAUTHORIZED)

    return result.data.user_id if result.data else None


async def get_problem(problem_id: int, token: str) -> Problem:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{get_tenjudge_server_base_url()}/agent/problem/{problem_id}",
                headers={"tenjudge-token": token},
            )
            response.raise_for_status()

        result = ProblemResult.model_validate(response.json())
    except httpx.HTTPError as exc:
        raise BizException(Code.SERVER_ERROR) from exc
    except (ValueError, ValidationError) as exc:
        raise BizException(Code.SERVER_ERROR) from exc

    if result.code != Code.SUCCESS.biz_code:
        raise BizException(Code.PARAM_ERROR, "problem is invalid")
    if result.data is None:
        raise BizException(Code.SERVER_ERROR, "problem data is empty")

    return result.data


async def get_submission(submission_id: int, token: str) -> Submission:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{get_tenjudge_server_base_url()}/submit/{submission_id}",
                headers={"tenjudge-token": token},
            )
            response.raise_for_status()

        result = SubmissionResult.model_validate(response.json())
    except httpx.HTTPError as exc:
        raise BizException(Code.SERVER_ERROR) from exc
    except (ValueError, ValidationError) as exc:
        raise BizException(Code.SERVER_ERROR) from exc

    if result.code != Code.SUCCESS.biz_code:
        raise BizException(Code.PARAM_ERROR, "submission is invalid")
    if result.data is None:
        raise BizException(Code.SERVER_ERROR, "submission data is empty")

    return result.data
