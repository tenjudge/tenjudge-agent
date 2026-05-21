import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from psycopg.rows import dict_row
from app.core.db import pool


class Conversation(BaseModel):
    id: uuid.UUID
    user_id: int
    title: str | None = None
    updated_at: datetime
    current_turn: int
    status: Literal["finished", "running"]

class ConversationRepository:
    async def get_by_id(self, conversation_id: uuid.UUID, conn=None) -> Conversation | None:
        if conn is None:
            async with pool.connection() as conn:
                return await self.get_by_id(conversation_id, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT id, user_id, title, updated_at, current_turn, status
                FROM conversations
                WHERE id = %s
                """,
                (conversation_id,),
            )
            row = await cur.fetchone()

        if row is None:
            return None

        return Conversation.model_validate(row)

    async def insert(self, conversation: Conversation, conn=None) -> Conversation:
        if conn is None:
            async with pool.connection() as conn:
                return await self.insert(conversation, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO conversations (id, user_id, title, updated_at, current_turn, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, user_id, title, updated_at, current_turn, status
                """,
                (
                    conversation.id, conversation.user_id, conversation.title,
                    conversation.updated_at, conversation.current_turn, conversation.status,
                ),
            )
            row = await cur.fetchone()

        return Conversation.model_validate(row)

    async def update_title(self, conversation_id: uuid.UUID, title: str, conn=None) -> Conversation | None:
        now = datetime.now()
        if conn is None:
            async with pool.connection() as conn:
                return await self.update_title(conversation_id, title, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                UPDATE conversations
                SET title = %s, updated_at = %s
                WHERE id = %s
                RETURNING id, user_id, title, updated_at, current_turn, status
                """,
                (title, now, conversation_id),
            )
            row = await cur.fetchone()

        if row is None:
            return None

        return Conversation.model_validate(row)

    async def update_current_turn(self, conversation_id: uuid.UUID, current_turn: int, conn=None) -> Conversation | None:
        now = datetime.now()
        if conn is None:
            async with pool.connection() as conn:
                return await self.update_current_turn(conversation_id, current_turn, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                UPDATE conversations
                SET current_turn = %s, updated_at = %s
                WHERE id = %s
                RETURNING id, user_id, title, updated_at, current_turn, status
                """,
                (current_turn, now, conversation_id),
            )
            row = await cur.fetchone()

        if row is None:
            return None

        return Conversation.model_validate(row)

    async def update_status(
        self,
        conversation_id: uuid.UUID,
        status: Literal["finished", "running"],
        conn=None,
    ) -> Conversation | None:
        now = datetime.now()
        if conn is None:
            async with pool.connection() as conn:
                return await self.update_status(conversation_id, status, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                UPDATE conversations
                SET status = %s, updated_at = %s
                WHERE id = %s
                RETURNING id, user_id, title, updated_at, current_turn, status
                """,
                (status, now, conversation_id),
            )
            row = await cur.fetchone()

        if row is None:
            return None

        return Conversation.model_validate(row)


'''
数据库建表语句：
CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL,
    title VARCHAR(255),
    updated_at TIMESTAMP NOT NULL,
    current_turn INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL
);
'''
