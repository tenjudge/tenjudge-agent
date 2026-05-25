import asyncio
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.context import CodeFile
from app.agents.orchestrator import AGENT_TOOLS, get_init_state
from app.agents.plan_agent import Plan, Step
from app.service import runner


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeConnection:
    def transaction(self):
        return FakeTransaction()


class FakeConnectionContext:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def connection(self):
        return FakeConnectionContext()


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

    class FakeAgent:
        async def astream(self, state, stream_mode):
            calls["agent_state"] = state
            calls["stream_mode"] = stream_mode
            yield "custom", "Analyzing context"
            yield "messages", (SimpleNamespace(content="final "), {"langgraph_node": "agent_node"})
            yield "messages", (SimpleNamespace(content="ignored"), {"langgraph_node": "tools_node"})
            yield "messages", (SimpleNamespace(content="answer"), {"langgraph_node": "agent_node"})
            final_state = dict(state)
            final_state["messages"] = [*state["messages"], AIMessage(content="final answer")]
            yield "values", final_state

    class FakeRedis:
        def __init__(self):
            self.events = []
            self.expires = []

        async def xadd(self, key, data):
            self.events.append((key, data))

        async def expire(self, key, ttl):
            self.expires.append((key, ttl))

    class FakeStateRepository:
        async def insert(self, state, conn=None):
            calls["state_record"] = state
            return state

    class FakeTaskRepository:
        async def update_state_by_task_id(self, task_id, state_id, conn=None):
            calls["updated_task"] = (task_id, state_id)

    class FakeMessageRepository:
        async def insert(self, message, conn=None):
            calls["agent_message"] = message
            return message

    class FakeConversationRepository:
        async def update_status(self, conversation_id, status, conn=None):
            calls["conversation_status"] = (conversation_id, status)

    async def fake_run_title_task(conversation_id, turn_index, task_id, message):
        calls["title_task"] = (conversation_id, turn_index, task_id, message)

    fake_redis = FakeRedis()

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
    monkeypatch.setattr(runner, "agent", FakeAgent())
    monkeypatch.setattr(runner, "redis_client", fake_redis)
    monkeypatch.setattr(runner, "settings", SimpleNamespace(REDIS_STREAM_TTL_SECONDS=60))
    monkeypatch.setattr(runner, "pool", FakePool())
    monkeypatch.setattr(runner, "StateRepository", FakeStateRepository)
    monkeypatch.setattr(runner, "TaskRepository", FakeTaskRepository)
    monkeypatch.setattr(runner, "MessageRepository", FakeMessageRepository)
    monkeypatch.setattr(runner, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(runner, "_run_title_task", fake_run_title_task)

    conversation_id = uuid.uuid4()
    task_id = uuid.uuid4()
    current_state = get_init_state()
    current_state["code_file_cnt"] = 1
    current_state["messages"].append(HumanMessage(content="previous context"))

    code_sources = ["int main() { return 0; }", "print('ok')"]
    await runner.run_task(
        conversation_id=conversation_id,
        turn_index=1,
        task_id=task_id,
        message="please compare these files",
        code_sources=code_sources,
        current_state=current_state,
    )
    await asyncio.sleep(0)

    assert calls["code_sources"] == code_sources
    assert calls["message"] == "please compare these files"
    assert [message.content for message in calls["history_messages"]] == [
        "You are the TenJudge online judge platform assistant.",
        "previous context",
    ]
    assert calls["available_tools"] is AGENT_TOOLS
    assert calls["planning_guidance"] is None
    assert calls["plan_messages"][-1].content == "please compare these files"

    assert current_state["code_file_cnt"] == 3
    assert [code_file.id for code_file in current_state["code_files"]] == [
        "code_file_2",
        "code_file_3",
    ]
    assert [code_file.file.content for code_file in current_state["code_files"]] == code_sources

    assert len(current_state["messages"]) == 6
    assert "Code file context id: code_file_2" in current_state["messages"][2].content
    assert "Description: User's C++ attempted solution" in current_state["messages"][2].content
    assert "Source Code (code_file_2)" in current_state["messages"][2].content
    assert current_state["messages"][-2].content == "please compare these files"
    assert isinstance(current_state["messages"][-1], SystemMessage)
    assert current_state["messages"][-1].content.startswith("[Internal plan]")
    assert "Compare the attached code files." in current_state["messages"][-1].content

    assert calls["stream_mode"] == ["messages", "custom", "values"]
    assert fake_redis.events == [
        (f"agent:task:{task_id}:events", {"event": "progress", "data": "Planning response"}),
        (f"agent:task:{task_id}:events", {"event": "progress", "data": "Analyzing context"}),
        (f"agent:task:{task_id}:events", {"event": "message", "data": "final "}),
        (f"agent:task:{task_id}:events", {"event": "message", "data": "answer"}),
        (f"agent:task:{task_id}:events", {"event": "done", "data": ""}),
    ]
    assert fake_redis.expires == [(f"agent:task:{task_id}:events", 60)] * 5
    assert calls["agent_message"].conversation_id == conversation_id
    assert calls["agent_message"].turn_index == 1
    assert calls["agent_message"].role == "agent"
    assert calls["agent_message"].content == "final answer"
    assert calls["updated_task"] == (task_id, calls["state_record"].id)
    assert calls["conversation_status"] == (conversation_id, "finished")
    assert calls["title_task"] == (
        conversation_id,
        1,
        task_id,
        "please compare these files",
    )


@pytest.mark.asyncio
async def test_run_title_task_updates_title_and_publishes_event(monkeypatch):
    calls = {}

    async def fake_summarize_title(message):
        calls["message"] = message
        return "最短路调试"

    class FakeRedis:
        def __init__(self):
            self.events = []
            self.expires = []

        async def xadd(self, key, data):
            self.events.append((key, data))

        async def expire(self, key, ttl):
            self.expires.append((key, ttl))

    class FakeConversationRepository:
        async def update_title_by_task(self, conversation_id, turn_index, task_id, title, conn=None):
            calls["title_update"] = (conversation_id, turn_index, task_id, title)
            return SimpleNamespace(id=conversation_id, title=title)

    fake_redis = FakeRedis()
    monkeypatch.setattr(runner, "summarize_title", fake_summarize_title)
    monkeypatch.setattr(runner, "redis_client", fake_redis)
    monkeypatch.setattr(runner, "settings", SimpleNamespace(REDIS_STREAM_TTL_SECONDS=60))
    monkeypatch.setattr(runner, "ConversationRepository", FakeConversationRepository)

    conversation_id = uuid.uuid4()
    task_id = uuid.uuid4()

    await runner._run_title_task(
        conversation_id=conversation_id,
        turn_index=1,
        task_id=task_id,
        message="为什么这份最短路代码 WA？",
    )

    assert calls["message"] == "为什么这份最短路代码 WA？"
    assert calls["title_update"] == (conversation_id, 1, task_id, "最短路调试")
    assert fake_redis.events == [
        (f"agent:task:{task_id}:events", {"event": "title", "data": "最短路调试"}),
    ]
    assert fake_redis.expires == [(f"agent:task:{task_id}:events", 60)]


@pytest.mark.asyncio
async def test_run_title_task_skips_stale_task(monkeypatch):
    calls = {}

    async def fake_summarize_title(message):
        calls["message"] = message
        return "旧标题"

    class FakeRedis:
        def __init__(self):
            self.events = []

        async def xadd(self, key, data):
            self.events.append((key, data))

        async def expire(self, key, ttl):
            raise AssertionError("stale title task should not refresh redis ttl")

    class FakeConversationRepository:
        async def update_title_by_task(self, conversation_id, turn_index, task_id, title, conn=None):
            calls["title_update"] = (conversation_id, turn_index, task_id, title)
            return None

    fake_redis = FakeRedis()
    monkeypatch.setattr(runner, "summarize_title", fake_summarize_title)
    monkeypatch.setattr(runner, "redis_client", fake_redis)
    monkeypatch.setattr(runner, "ConversationRepository", FakeConversationRepository)

    conversation_id = uuid.uuid4()
    task_id = uuid.uuid4()

    await runner._run_title_task(
        conversation_id=conversation_id,
        turn_index=1,
        task_id=task_id,
        message="旧问题",
    )

    assert calls["message"] == "旧问题"
    assert calls["title_update"] == (conversation_id, 1, task_id, "旧标题")
    assert fake_redis.events == []
