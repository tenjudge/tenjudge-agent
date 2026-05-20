import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.core.db import pool


class Message(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    turn_index: int
    role: Literal["user", "agent"]
    content: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)

class MessageRepository:
    async def insert(self, message: Message) -> Message:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO messages (id, conversation_id, turn_index, role, content, attachments)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, conversation_id, turn_index, role, content, attachments
                    """,
                    (
                        message.id, message.conversation_id, message.turn_index,
                        message.role, message.content, Jsonb(message.attachments),
                    ),
                )
                row = await cur.fetchone()

        return Message.model_validate(row)

    async def get_by_id(self, message_id: uuid.UUID) -> Message | None:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, conversation_id, turn_index, role, content, attachments
                    FROM messages
                    WHERE id = %s
                    """,
                    (message_id,),
                )
                row = await cur.fetchone()

        if row is None:
            return None

        return Message.model_validate(row)

    async def list_by_conversation(self, conversation_id: uuid.UUID) -> list[Message]:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, conversation_id, turn_index, role, content, attachments
                    FROM messages
                    WHERE conversation_id = %s
                    ORDER BY turn_index, CASE role WHEN 'user' THEN 0 ELSE 1 END
                    """,
                    (conversation_id,),
                )
                rows = await cur.fetchall()

        return [Message.model_validate(row) for row in rows]


'''
数据库建表语句：
CREATE TABLE messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations (id),
    turn_index INTEGER NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,

    CHECK (role IN ('user', 'agent')),
    CHECK (jsonb_typeof(attachments) = 'array')
);

CREATE INDEX idx_messages_conversation_turn
ON messages (conversation_id, turn_index);
'''
