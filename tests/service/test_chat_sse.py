import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.response import BizException, Code
from app.repository.conversations import Conversation
from app.repository.tasks import Task
from app.service import chat


class FakeRedis:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def xread(self, streams, count, block):
        self.calls.append({
            "streams": streams,
            "count": count,
            "block": block,
        })
        if not self.responses:
            return []
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_chat_event_generator_reads_stream_and_stops_on_done(monkeypatch):
    task_id = uuid.uuid4()
    stream_key = f"agent:task:{task_id}:events"
    fake_redis = FakeRedis([
        [],
        [
            (
                stream_key,
                [
                    ("1-0", {"event": "progress", "data": "正在分析\n代码"}),
                    ("2-0", {"event": "done", "data": ""}),
                ],
            ),
        ],
    ])
    monkeypatch.setattr(chat, "redis_client", fake_redis)
    monkeypatch.setattr(chat, "settings", SimpleNamespace(
        REDIS_STREAM_READ_BLOCK_MS=1,
        REDIS_STREAM_READ_COUNT=10,
    ))

    generator = chat.chat_event_generator(task_id)

    assert await anext(generator) == ": ping\n\n"
    assert await anext(generator) == (
        "id: 1-0\n"
        "event: progress\n"
        "data: 正在分析\n"
        "data: 代码\n\n"
    )
    assert await anext(generator) == (
        "id: 2-0\n"
        "event: done\n"
        "data: \n\n"
    )

    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert fake_redis.calls == [
        {
            "streams": {stream_key: "0-0"},
            "count": 10,
            "block": 1,
        },
        {
            "streams": {stream_key: "0-0"},
            "count": 10,
            "block": 1,
        },
    ]


@pytest.mark.asyncio
async def test_chat_event_generator_resumes_from_last_event_id(monkeypatch):
    task_id = uuid.uuid4()
    stream_key = f"agent:task:{task_id}:events"
    fake_redis = FakeRedis([
        [
            (
                stream_key,
                [
                    ("2-0", {"event": "message", "data": "最终回答"}),
                    ("3-0", {"event": "done", "data": ""}),
                ],
            ),
        ],
    ])
    monkeypatch.setattr(chat, "redis_client", fake_redis)
    monkeypatch.setattr(chat, "settings", SimpleNamespace(
        REDIS_STREAM_READ_BLOCK_MS=1,
        REDIS_STREAM_READ_COUNT=10,
    ))

    generator = chat.chat_event_generator(task_id, last_event_id="1-0")

    assert await anext(generator) == (
        "id: 2-0\n"
        "event: message\n"
        "data: 最终回答\n\n"
    )

    assert fake_redis.calls[0]["streams"] == {stream_key: "1-0"}


@pytest.mark.asyncio
async def test_validate_chat_event_subscription_rejects_invalid_last_event_id():
    with pytest.raises(BizException) as exc_info:
        await chat.validate_chat_event_subscription(uuid.uuid4(), user_id=1, last_event_id="bad")

    assert exc_info.value.code is Code.PARAM_ERROR


@pytest.mark.asyncio
async def test_validate_chat_event_subscription_checks_conversation_owner(monkeypatch):
    task_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    class FakeTaskRepository:
        async def get_by_task_id(self, received_task_id):
            assert received_task_id == task_id
            return Task(
                conversation_id=conversation_id,
                turn_index=1,
                task_id=task_id,
                state=None,
            )

    class FakeConversationRepository:
        async def get_by_id(self, received_conversation_id):
            assert received_conversation_id == conversation_id
            return Conversation(
                id=conversation_id,
                user_id=2,
                title=None,
                updated_at=datetime.now(),
                current_turn=1,
                status="running",
            )

    monkeypatch.setattr(chat, "TaskRepository", FakeTaskRepository)
    monkeypatch.setattr(chat, "ConversationRepository", FakeConversationRepository)

    with pytest.raises(BizException) as exc_info:
        await chat.validate_chat_event_subscription(task_id, user_id=1)

    assert exc_info.value.code is Code.FORBIDDEN


@pytest.mark.asyncio
async def test_validate_chat_event_subscription_rejects_expired_stream(monkeypatch):
    task_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    class FakeTaskRepository:
        async def get_by_task_id(self, received_task_id):
            assert received_task_id == task_id
            return Task(
                conversation_id=conversation_id,
                turn_index=1,
                task_id=task_id,
                state=None,
            )

    class FakeConversationRepository:
        async def get_by_id(self, received_conversation_id):
            assert received_conversation_id == conversation_id
            return Conversation(
                id=conversation_id,
                user_id=1,
                title=None,
                updated_at=datetime.now(),
                current_turn=1,
                status="running",
            )

    class FakeRedis:
        async def exists(self, key):
            assert key == f"agent:task:{task_id}:events"
            return 0

    monkeypatch.setattr(chat, "TaskRepository", FakeTaskRepository)
    monkeypatch.setattr(chat, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(chat, "redis_client", FakeRedis())

    with pytest.raises(BizException) as exc_info:
        await chat.validate_chat_event_subscription(task_id, user_id=1)

    assert exc_info.value.code is Code.NOT_FOUND
    assert exc_info.value.message == "task event stream expired"
