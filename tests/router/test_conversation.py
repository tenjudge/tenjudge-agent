import uuid
from datetime import datetime, timedelta

import pytest

from app.core.response import BizException, Code
from app.repository.conversations import Conversation
from app.repository.messages import Message
from app.repository.tasks import Task
from app.router import conversation


def _conversation(
    conversation_id: uuid.UUID,
    title: str | None,
    updated_at: datetime,
    user_id: int = 10,
    current_turn: int = 1,
    status: str = "finished",
) -> Conversation:
    return Conversation(
        id=conversation_id,
        user_id=user_id,
        title=title,
        updated_at=updated_at,
        current_turn=current_turn,
        status=status,
    )


@pytest.mark.asyncio
async def test_list_conversations_returns_visible_items_and_next_cursor(monkeypatch):
    now = datetime(2026, 5, 26, 12, 0, 0)
    conversations = [
        _conversation(uuid.uuid4(), "latest", now),
        _conversation(uuid.uuid4(), None, now - timedelta(minutes=1)),
        _conversation(uuid.uuid4(), "older", now - timedelta(minutes=2)),
    ]

    async def fake_get_current_user_id(token):
        assert token == "token"
        return 10

    class FakeConversationRepository:
        async def list_by_user_id(self, user_id, limit, before_updated_at=None, before_id=None):
            assert user_id == 10
            assert limit == 3
            assert before_updated_at is None
            assert before_id is None
            return conversations

    monkeypatch.setattr(conversation, "get_current_user_id", fake_get_current_user_id)
    monkeypatch.setattr(conversation, "ConversationRepository", FakeConversationRepository)

    result = await conversation.list_conversations(token="token", limit=2, cursor=None)

    assert result.code == 0
    assert [item.id for item in result.data.items] == [conversations[0].id, conversations[1].id]
    assert [item.title for item in result.data.items] == ["latest", None]
    assert result.data.next_cursor is not None

    cursor_updated_at, cursor_id = conversation._decode_conversation_cursor(result.data.next_cursor)
    assert cursor_updated_at == conversations[1].updated_at
    assert cursor_id == conversations[1].id


@pytest.mark.asyncio
async def test_list_conversations_uses_cursor_for_next_page(monkeypatch):
    cursor_conversation = _conversation(
        uuid.uuid4(),
        "cursor",
        datetime(2026, 5, 26, 12, 0, 0),
    )
    page_conversation = _conversation(
        uuid.uuid4(),
        "next",
        datetime(2026, 5, 26, 11, 0, 0),
    )
    cursor = conversation._encode_conversation_cursor(cursor_conversation)

    async def fake_get_current_user_id(token):
        assert token == "token"
        return 10

    class FakeConversationRepository:
        async def list_by_user_id(self, user_id, limit, before_updated_at=None, before_id=None):
            assert user_id == 10
            assert limit == 21
            assert before_updated_at == cursor_conversation.updated_at
            assert before_id == cursor_conversation.id
            return [page_conversation]

    monkeypatch.setattr(conversation, "get_current_user_id", fake_get_current_user_id)
    monkeypatch.setattr(conversation, "ConversationRepository", FakeConversationRepository)

    result = await conversation.list_conversations(token="token", cursor=cursor)

    assert [item.id for item in result.data.items] == [page_conversation.id]
    assert result.data.next_cursor is None


@pytest.mark.asyncio
async def test_list_conversations_rejects_invalid_cursor(monkeypatch):
    async def fake_get_current_user_id(token):
        return 10

    monkeypatch.setattr(conversation, "get_current_user_id", fake_get_current_user_id)

    with pytest.raises(BizException) as exc_info:
        await conversation.list_conversations(token="token", cursor="invalid")

    assert exc_info.value.code is Code.PARAM_ERROR
    assert exc_info.value.message == "cursor is invalid"


@pytest.mark.asyncio
async def test_get_conversation_detail_returns_messages_and_attachments(monkeypatch):
    conversation_id = uuid.uuid4()
    stored_conversation = _conversation(
        conversation_id,
        "title",
        datetime(2026, 5, 26, 12, 0, 0),
        status="finished",
    )
    stored_messages = [
        Message(
            conversation_id=conversation_id,
            turn_index=1,
            role="user",
            content="帮我看看这段代码",
            attachments=[
                {
                    "type": "code",
                    "content": "print(1)",
                },
            ],
        ),
        Message(
            conversation_id=conversation_id,
            turn_index=1,
            role="agent",
            content="这段代码会输出 1。",
            attachments=[],
        ),
    ]

    async def fake_get_current_user_id(token):
        assert token == "token"
        return 10

    class FakeConversationRepository:
        async def get_by_id(self, received_conversation_id):
            assert received_conversation_id == conversation_id
            return stored_conversation

    class FakeMessageRepository:
        async def list_by_conversation(self, received_conversation_id):
            assert received_conversation_id == conversation_id
            return stored_messages

    class FakeTaskRepository:
        async def get_by_key(self, received_conversation_id, turn_index):
            raise AssertionError("finished conversation should not load running task")

    monkeypatch.setattr(conversation, "get_current_user_id", fake_get_current_user_id)
    monkeypatch.setattr(conversation, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(conversation, "MessageRepository", FakeMessageRepository)
    monkeypatch.setattr(conversation, "TaskRepository", FakeTaskRepository)

    result = await conversation.get_conversation_detail(conversation_id=conversation_id, token="token")

    assert result.code == 0
    assert result.data.id == conversation_id
    assert result.data.title == "title"
    assert result.data.status == "finished"
    assert result.data.running_task_id is None
    assert [(message.turn_index, message.role, message.content) for message in result.data.messages] == [
        (1, "user", "帮我看看这段代码"),
        (1, "agent", "这段代码会输出 1。"),
    ]
    assert result.data.messages[0].attachments == [{"type": "code", "content": "print(1)"}]
    assert result.data.messages[1].attachments == []


@pytest.mark.asyncio
async def test_get_conversation_detail_returns_running_task_id(monkeypatch):
    conversation_id = uuid.uuid4()
    task_id = uuid.uuid4()
    stored_conversation = _conversation(
        conversation_id,
        None,
        datetime(2026, 5, 26, 12, 0, 0),
        current_turn=3,
        status="running",
    )

    async def fake_get_current_user_id(token):
        assert token == "token"
        return 10

    class FakeConversationRepository:
        async def get_by_id(self, received_conversation_id):
            assert received_conversation_id == conversation_id
            return stored_conversation

    class FakeMessageRepository:
        async def list_by_conversation(self, received_conversation_id):
            assert received_conversation_id == conversation_id
            return []

    class FakeTaskRepository:
        async def get_by_key(self, received_conversation_id, turn_index):
            assert received_conversation_id == conversation_id
            assert turn_index == 3
            return Task(
                conversation_id=conversation_id,
                turn_index=3,
                task_id=task_id,
                state=None,
            )

    monkeypatch.setattr(conversation, "get_current_user_id", fake_get_current_user_id)
    monkeypatch.setattr(conversation, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(conversation, "MessageRepository", FakeMessageRepository)
    monkeypatch.setattr(conversation, "TaskRepository", FakeTaskRepository)

    result = await conversation.get_conversation_detail(conversation_id=conversation_id, token="token")

    assert result.data.status == "running"
    assert result.data.running_task_id == task_id
    assert result.data.messages == []


@pytest.mark.asyncio
async def test_get_conversation_detail_rejects_other_user(monkeypatch):
    conversation_id = uuid.uuid4()
    stored_conversation = _conversation(
        conversation_id,
        None,
        datetime(2026, 5, 26, 12, 0, 0),
        user_id=20,
    )

    async def fake_get_current_user_id(token):
        return 10

    class FakeConversationRepository:
        async def get_by_id(self, received_conversation_id):
            assert received_conversation_id == conversation_id
            return stored_conversation

    monkeypatch.setattr(conversation, "get_current_user_id", fake_get_current_user_id)
    monkeypatch.setattr(conversation, "ConversationRepository", FakeConversationRepository)

    with pytest.raises(BizException) as exc_info:
        await conversation.get_conversation_detail(conversation_id=conversation_id, token="token")

    assert exc_info.value.code is Code.FORBIDDEN


@pytest.mark.asyncio
async def test_get_conversation_detail_rejects_missing_conversation(monkeypatch):
    conversation_id = uuid.uuid4()

    async def fake_get_current_user_id(token):
        return 10

    class FakeConversationRepository:
        async def get_by_id(self, received_conversation_id):
            assert received_conversation_id == conversation_id
            return None

    monkeypatch.setattr(conversation, "get_current_user_id", fake_get_current_user_id)
    monkeypatch.setattr(conversation, "ConversationRepository", FakeConversationRepository)

    with pytest.raises(BizException) as exc_info:
        await conversation.get_conversation_detail(conversation_id=conversation_id, token="token")

    assert exc_info.value.code is Code.CONVERSATION_NOT_FOUND
