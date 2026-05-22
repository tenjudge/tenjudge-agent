import uuid

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.context import CodeFile
from app.agents.orchestrator import get_init_state
from app.agents.plan_agent import Plan, Step
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

    async def fake_make_plan(messages, available_tools=None, planning_guidance=None):
        calls["plan_messages"] = list(messages)
        calls["available_tools"] = available_tools
        calls["planning_guidance"] = planning_guidance
        return Plan(
            summary="Compare the attached code files.",
            steps=[
                Step(description="Inspect both code files and compare their behavior."),
            ],
        )

    monkeypatch.setattr(
        runner,
        "summarize_code_files",
        fake_summarize_code_files,
    )
    monkeypatch.setattr(
        runner,
        "make_plan",
        fake_make_plan,
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
    assert calls["available_tools"] == []
    assert calls["planning_guidance"] is None
    assert calls["plan_messages"][-1].content == "please compare these files"

    assert current_state["code_file_cnt"] == 3
    assert [code_file.id for code_file in current_state["code_files"]] == [
        "code_file_2",
        "code_file_3",
    ]
    assert [code_file.file.content for code_file in current_state["code_files"]] == code_sources

    assert len(current_state["messages"]) == 5
    assert "Code file context id: code_file_2" in current_state["messages"][1].content
    assert "Description: User's C++ attempted solution" in current_state["messages"][1].content
    assert "Source Code (code_file_2)" in current_state["messages"][1].content
    assert current_state["messages"][-2].content == "please compare these files"
    assert isinstance(current_state["messages"][-1], SystemMessage)
    assert current_state["messages"][-1].content.startswith("[Internal plan]")
    assert "Compare the attached code files." in current_state["messages"][-1].content
