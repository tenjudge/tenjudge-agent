import json
from datetime import datetime

import pytest

from app.agents.context import CodeFile, CodeFileContext, Submission
from app.agents.orchestrator import AGENT_TOOLS
from app.service.tenjudge_server import JudgeWaitResult
from app.tools import judge


def make_state(code_files: list[CodeFileContext], token: str = "token") -> dict:
    return {
        "code_files": code_files,
        "token": token,
    }


def make_submission(status: str = "WRONG_ANSWER") -> Submission:
    return Submission(
        id=3001,
        problemId=1001,
        problemName="A + B Problem",
        submitTime=datetime(2026, 5, 26, 12, 34, 56),
        language="cpp",
        status=status,
        time=128,
        memory=64,
        info=None,
        code="secret source should not be returned",
        details=[],
    )


@pytest.mark.asyncio
async def test_submit_code_for_judge_submits_state_code_file(monkeypatch):
    calls = {}

    async def fake_submit_judge_and_wait(problem_id, language, code, token):
        calls["problem_id"] = problem_id
        calls["language"] = language
        calls["code"] = code
        calls["token"] = token
        return JudgeWaitResult(success=True, submission=make_submission())

    monkeypatch.setattr(judge, "submit_judge_and_wait", fake_submit_judge_and_wait)

    result = await judge.submit_code_for_judge.coroutine(
        code_file_id="code_file_1",
        problem_id=1001,
        state=make_state([
            CodeFileContext(
                id="code_file_1",
                file=CodeFile(
                    description="accepted attempt",
                    language="cpp",
                    content="int main() { return 0; }",
                ),
            ),
        ]),
    )

    payload = json.loads(result)
    assert calls == {
        "problem_id": 1001,
        "language": "cpp",
        "code": "int main() { return 0; }",
        "token": "token",
    }
    assert payload["success"] is True
    assert payload["message"] == ""
    assert payload["code_file_id"] == "code_file_1"
    assert payload["problem_id"] == 1001
    assert payload["language"] == "cpp"
    assert payload["submission"]["id"] == 3001
    assert payload["submission"]["status"] == "WRONG_ANSWER"
    assert "code" not in payload["submission"]


@pytest.mark.asyncio
async def test_submit_code_for_judge_returns_failure_for_missing_code_file():
    result = await judge.submit_code_for_judge.coroutine(
        code_file_id="code_file_9",
        problem_id=1001,
        state=make_state([
            CodeFileContext(
                id="code_file_1",
                file=CodeFile(
                    description="attempt",
                    language="python",
                    content="print(0)",
                ),
            ),
        ]),
    )

    payload = json.loads(result)
    assert payload == {
        "success": False,
        "message": "code_file_id code_file_9 was not found. Available code files: code_file_1",
        "code_file_id": "code_file_9",
        "problem_id": 1001,
        "language": None,
        "submission": None,
    }


@pytest.mark.asyncio
async def test_submit_code_for_judge_returns_failure_for_unsupported_language():
    result = await judge.submit_code_for_judge.coroutine(
        code_file_id="code_file_1",
        problem_id=1001,
        state=make_state([
            CodeFileContext(
                id="code_file_1",
                file=CodeFile(
                    description="plain text",
                    language="else",
                    content="not supported",
                ),
            ),
        ]),
    )

    payload = json.loads(result)
    assert payload["success"] is False
    assert payload["message"] == "code_file_1 uses unsupported language else. TenJudge judge supports cpp and python."
    assert payload["language"] == "else"
    assert payload["submission"] is None


@pytest.mark.asyncio
async def test_submit_code_for_judge_returns_failure_for_missing_token():
    result = await judge.submit_code_for_judge.coroutine(
        code_file_id="code_file_1",
        problem_id=1001,
        state=make_state([
            CodeFileContext(
                id="code_file_1",
                file=CodeFile(
                    description="attempt",
                    language="python",
                    content="print(0)",
                ),
            ),
        ], token=""),
    )

    payload = json.loads(result)
    assert payload["success"] is False
    assert payload["message"] == "authenticated TenJudge token is missing from agent state"
    assert payload["language"] == "python"
    assert payload["submission"] is None


def test_submit_code_for_judge_is_registered_for_main_agent():
    assert judge.submit_code_for_judge in AGENT_TOOLS
    assert set(judge.submit_code_for_judge.args) == {"code_file_id", "problem_id"}
