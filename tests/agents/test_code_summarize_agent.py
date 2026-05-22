import pytest

from app.agents import code_summarize_agent
from app.agents.code_summarize_agent import (
    CodeFileSummary,
    CodeFileSummaryResult,
    summarize_code_files,
)


@pytest.mark.asyncio
async def test_summarize_code_files_returns_empty_without_model_call(monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("model should not be called for empty code_sources")

    monkeypatch.setattr(
        code_summarize_agent,
        "_invoke_code_summary_model",
        fail_if_called,
    )

    code_files = await summarize_code_files([], "please check this", [])

    assert code_files == []


@pytest.mark.asyncio
async def test_summarize_code_files_preserves_source_order(monkeypatch):
    async def fake_invoke(*args, **kwargs):
        return CodeFileSummaryResult(files=[
            CodeFileSummary(
                description="User's C++ attempted solution for the current problem.",
                language="cpp",
            ),
            CodeFileSummary(
                description="Python brute-force checker for comparing answers.",
                language="python",
            ),
        ])

    monkeypatch.setattr(
        code_summarize_agent,
        "_invoke_code_summary_model",
        fake_invoke,
    )

    code_sources = ["int main() { return 0; }", "print('ok')"]
    code_files = await summarize_code_files(code_sources, "compare these", [])

    assert [file.content for file in code_files] == code_sources
    assert [file.language for file in code_files] == ["cpp", "python"]
    assert code_files[0].description.startswith("User's C++ attempted solution")


@pytest.mark.asyncio
async def test_summarize_code_files_retries_count_mismatch(monkeypatch):
    calls: list[str | None] = []

    async def fake_invoke(*args, retry_note=None, **kwargs):
        calls.append(retry_note)
        if len(calls) == 1:
            return CodeFileSummaryResult(files=[
                CodeFileSummary(description="Only one summary.", language="cpp"),
            ])
        return CodeFileSummaryResult(files=[
            CodeFileSummary(description="First source file.", language="cpp"),
            CodeFileSummary(description="Second source file.", language="else"),
        ])

    monkeypatch.setattr(
        code_summarize_agent,
        "_invoke_code_summary_model",
        fake_invoke,
    )

    code_files = await summarize_code_files(["int main() {}", "SELECT 1"], "summarize", [])

    assert len(code_files) == 2
    assert calls[0] is None
    assert "exactly 2 summaries" in calls[1]
