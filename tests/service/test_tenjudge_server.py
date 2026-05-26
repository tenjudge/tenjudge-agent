import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from app.core.response import BizException, Code
from app.service import tenjudge_server


BASE_URL = "https://tenjudge.example"


def make_submission_payload(status: str = "ACCEPTED") -> dict:
    return {
        "id": 3001,
        "problemId": 1001,
        "problemName": "A + B Problem",
        "submitTime": "2026-05-22T12:34:56",
        "language": "cpp",
        "status": status,
        "time": 128,
        "memory": 64,
        "info": "ok",
        "code": "int main() { return 0; }",
        "details": [
            {
                "testCaseId": 1,
                "status": status,
                "time": 32,
                "memory": 16,
                "info": None,
                "input": "1 2",
                "output": "3",
                "answer": "3",
            }
        ],
    }


@pytest.fixture(autouse=True)
def tenjudge_base_url(monkeypatch):
    monkeypatch.setattr(tenjudge_server.settings, "TENJUDGE_SERVER_BASE_URL", BASE_URL)


@pytest.mark.asyncio
@respx.mock
async def test_get_current_user_id_returns_user_id():
    respx.get(f"{BASE_URL}/auth/me/id").respond(
        json={"code": Code.SUCCESS.biz_code, "data": {"userId": 42}}
    )

    user_id = await tenjudge_server.get_current_user_id("token")

    assert user_id == 42


@pytest.mark.asyncio
@respx.mock
async def test_get_current_user_id_raises_unauthorized_for_business_failure():
    respx.get(f"{BASE_URL}/auth/me/id").respond(
        json={"code": Code.UNAUTHORIZED.biz_code, "data": None}
    )

    with pytest.raises(BizException) as exc_info:
        await tenjudge_server.get_current_user_id("token")

    assert exc_info.value.code is Code.UNAUTHORIZED


@pytest.mark.asyncio
@respx.mock
async def test_get_problem_maps_problem_response():
    respx.get(f"{BASE_URL}/agent/problem/1001").respond(
        json={
            "code": Code.SUCCESS.biz_code,
            "data": {
                "id": 1001,
                "authorId": 7,
                "visibility": "public",
                "checker": "default",
                "timeLimit": 1000,
                "memoryLimit": 256,
                "name": "A + B Problem",
                "statement": "Calculate a + b.",
                "solution": "Use addition.",
                "difficulty": 1,
                "version": 3,
                "tags": ["math"],
            },
        }
    )

    problem = await tenjudge_server.get_problem(1001, "token")

    assert problem.problem_id == 1001
    assert problem.author_id == 7
    assert problem.time_limit == 1000
    assert problem.memory_limit == 256


@pytest.mark.asyncio
@respx.mock
async def test_get_submission_maps_submission_response():
    respx.get(f"{BASE_URL}/submit/3001").respond(
        json={
            "code": Code.SUCCESS.biz_code,
            "data": make_submission_payload(),
        }
    )

    submission = await tenjudge_server.get_submission(3001, "token")

    assert submission.submission_id == 3001
    assert submission.problem_id == 1001
    assert submission.problem_name == "A + B Problem"
    assert submission.details[0].test_case_id == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_problem_raises_server_error_for_http_error():
    respx.get(f"{BASE_URL}/agent/problem/1001").mock(
        side_effect=httpx.ConnectError("connection failed")
    )

    with pytest.raises(httpx.ConnectError):
        await tenjudge_server.get_problem(1001, "token")


@pytest.mark.asyncio
@respx.mock
async def test_get_submission_raises_server_error_for_invalid_shape():
    respx.get(f"{BASE_URL}/submit/3001").respond(
        json={"code": Code.SUCCESS.biz_code, "data": {"id": 3001}}
    )

    with pytest.raises(ValidationError):
        await tenjudge_server.get_submission(3001, "token")


@pytest.mark.asyncio
@respx.mock
async def test_submit_judge_posts_agent_payload_and_returns_submission_id():
    route = respx.post(f"{BASE_URL}/submit/judge").respond(
        json={
            "code": Code.SUCCESS.biz_code,
            "message": "success",
            "data": {"submissionId": 3001},
        }
    )

    submission_id = await tenjudge_server.submit_judge(
        problem_id=1001,
        language="cpp",
        code="int main() { return 0; }",
        token="token",
    )

    request = route.calls.last.request
    payload = json.loads(request.content)
    assert submission_id == 3001
    assert request.headers["tenjudge-token"] == "token"
    assert payload == {
        "problemId": 1001,
        "language": "cpp",
        "code": "int main() { return 0; }",
        "isAgent": True,
    }
    assert "contestId" not in payload


@pytest.mark.asyncio
@respx.mock
async def test_submit_judge_raises_param_error_for_business_failure():
    respx.post(f"{BASE_URL}/submit/judge").respond(
        json={
            "code": Code.PARAM_ERROR.biz_code,
            "message": "problem is invalid",
            "data": None,
        }
    )

    with pytest.raises(BizException) as exc_info:
        await tenjudge_server.submit_judge(
            problem_id=1001,
            language="cpp",
            code="int main() { return 0; }",
            token="token",
        )

    assert exc_info.value.code is Code.PARAM_ERROR
    assert exc_info.value.message == "problem is invalid"


@pytest.mark.asyncio
@respx.mock
async def test_submit_judge_and_wait_returns_final_submission_after_pending():
    respx.post(f"{BASE_URL}/submit/judge").respond(
        json={
            "code": Code.SUCCESS.biz_code,
            "data": {"submissionId": 3001},
        }
    )
    respx.get(f"{BASE_URL}/submit/3001").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "code": Code.SUCCESS.biz_code,
                    "data": make_submission_payload(status="PENDING"),
                },
            ),
            httpx.Response(
                200,
                json={
                    "code": Code.SUCCESS.biz_code,
                    "data": make_submission_payload(status="ACCEPTED"),
                },
            ),
        ]
    )

    result = await tenjudge_server.submit_judge_and_wait(
        problem_id=1001,
        language="cpp",
        code="int main() { return 0; }",
        token="token",
        timeout_seconds=5,
        poll_interval_seconds=0,
    )

    assert result.success is True
    assert result.submission is not None
    assert result.submission.status == "ACCEPTED"
    assert result.message == ""


@pytest.mark.asyncio
@respx.mock
async def test_submit_judge_and_wait_returns_failure_when_pending_times_out():
    respx.post(f"{BASE_URL}/submit/judge").respond(
        json={
            "code": Code.SUCCESS.biz_code,
            "data": {"submissionId": 3001},
        }
    )
    respx.get(f"{BASE_URL}/submit/3001").respond(
        json={
            "code": Code.SUCCESS.biz_code,
            "data": make_submission_payload(status="PENDING"),
        }
    )

    result = await tenjudge_server.submit_judge_and_wait(
        problem_id=1001,
        language="cpp",
        code="int main() { return 0; }",
        token="token",
        timeout_seconds=0,
    )

    assert result.success is False
    assert result.submission is not None
    assert result.submission.status == "PENDING"
    assert result.message == "judge result is still pending after 0 seconds"


@pytest.mark.asyncio
@respx.mock
async def test_submit_judge_and_wait_returns_failure_when_submission_unavailable():
    respx.post(f"{BASE_URL}/submit/judge").respond(
        json={
            "code": Code.SUCCESS.biz_code,
            "data": {"submissionId": 3001},
        }
    )
    respx.get(f"{BASE_URL}/submit/3001").respond(
        json={
            "code": Code.NOT_FOUND.biz_code,
            "message": "submission not found",
            "data": None,
        }
    )

    result = await tenjudge_server.submit_judge_and_wait(
        problem_id=1001,
        language="cpp",
        code="int main() { return 0; }",
        token="token",
    )

    assert result.success is False
    assert result.submission is None
    assert result.message == "submission 3001 is unavailable: submission not found"
