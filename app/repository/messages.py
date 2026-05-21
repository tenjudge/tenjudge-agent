import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.core.db import pool


class Message(BaseModel):
    conversation_id: uuid.UUID
    turn_index: int
    role: Literal["user", "agent"]
    content: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)

class MessageRepository:
    async def insert(self, message: Message, conn=None) -> Message:
        if conn is None:
            async with pool.connection() as conn:
                return await self.insert(message, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO messages (conversation_id, turn_index, role, content, attachments)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING conversation_id, turn_index, role, content, attachments
                """,
                (
                    message.conversation_id, message.turn_index, message.role,
                    message.content, Jsonb(message.attachments),
                ),
            )
            row = await cur.fetchone()

        return Message.model_validate(row)

    async def get_by_key(
        self,
        conversation_id: uuid.UUID,
        turn_index: int,
        role: Literal["user", "agent"],
        conn=None,
    ) -> Message | None:
        if conn is None:
            async with pool.connection() as conn:
                return await self.get_by_key(conversation_id, turn_index, role, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT conversation_id, turn_index, role, content, attachments
                FROM messages
                WHERE conversation_id = %s AND turn_index = %s AND role = %s
                """,
                (conversation_id, turn_index, role),
            )
            row = await cur.fetchone()

        if row is None:
            return None

        return Message.model_validate(row)

    async def delete_by_key(
        self,
        conversation_id: uuid.UUID,
        turn_index: int,
        role: Literal["user", "agent"],
        conn=None,
    ) -> None:
        if conn is None:
            async with pool.connection() as conn:
                await self.delete_by_key(conversation_id, turn_index, role, conn=conn)
                return

        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM messages
                WHERE conversation_id = %s AND turn_index = %s AND role = %s
                """,
                (conversation_id, turn_index, role),
            )

    async def delete_from_turn(self, conversation_id: uuid.UUID, turn_index: int, conn=None) -> None:
        if conn is None:
            async with pool.connection() as conn:
                await self.delete_from_turn(conversation_id, turn_index, conn=conn)
                return

        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM messages
                WHERE conversation_id = %s AND turn_index >= %s
                """,
                (conversation_id, turn_index),
            )

    async def list_by_conversation(self, conversation_id: uuid.UUID, conn=None) -> list[Message]:
        if conn is None:
            async with pool.connection() as conn:
                return await self.list_by_conversation(conversation_id, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT conversation_id, turn_index, role, content, attachments
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
    conversation_id UUID NOT NULL REFERENCES conversations (id),
    turn_index INTEGER NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,

    CHECK (role IN ('user', 'agent')),
    CHECK (jsonb_typeof(attachments) = 'array'),

    PRIMARY KEY (conversation_id, turn_index, role)
);
'''
