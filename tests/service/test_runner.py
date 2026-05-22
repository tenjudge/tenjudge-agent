import uuid

import pytest
from langchain_core.messages import HumanMessage

from app.agents.context import CodeFile
from app.agents.orchestrator import get_init_state
from app.service import runner


@pytest.mark.asyncio
async def test_run_task_appends_code_files_and_messages(monkeypatch):
    calls = {}

    async def fake_summarize_code_files(code_sources, message, history_messages):
        calls["code_sources"] = code_sources
        calls["message"] = message
        calls["history_messages"] = list(history_messages)
        return [
            CodeFile(
                description="User's C++ attempted solution for the current problem.",
                language="cpp",
                content=code_sources[0],
            ),
            CodeFile(
                description="Python brute-force checker for the attempted solution.",
                language="python",
                content=code_sources[1],
            ),
        ]

    monkeypatch.setattr(
        runner,
        "summarize_code_files",
        fake_summarize_code_files,
    )

    current_state = get_init_state()
    current_state["code_file_cnt"] = 1
    current_state["messages"].append(HumanMessage(content="previous context"))

    code_sources = ["int main() { return 0; }", "print('ok')"]
    await runner.run_task(
        task_id=uuid.uuid4(),
        message="please compare these files",
        code_sources=code_sources,
        current_state=current_state,
    )

    assert calls["code_sources"] == code_sources
    assert calls["message"] == "please compare these files"
    assert [message.content for message in calls["history_messages"]] == ["previous context"]

    assert current_state["code_file_cnt"] == 3
    assert [code_file.id for code_file in current_state["code_files"]] == [
        "code_file_2",
        "code_file_3",
    ]
    assert [code_file.file.content for code_file in current_state["code_files"]] == code_sources

    assert len(current_state["messages"]) == 4
    assert "Code file context id: code_file_2" in current_state["messages"][1].content
    assert "Description: User's C++ attempted solution" in current_state["messages"][1].content
    assert "Source Code (code_file_2)" in current_state["messages"][1].content
    assert current_state["messages"][-1].content == "please compare these files"
