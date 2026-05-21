import uuid

from pydantic import BaseModel
from psycopg.rows import dict_row
from app.core.db import pool


class Task(BaseModel):
    conversation_id: uuid.UUID
    turn_index: int
    task_id: uuid.UUID
    state: uuid.UUID | None = None

class TaskRepository:
    async def insert(self, task: Task, conn=None) -> Task:
        if conn is None:
            async with pool.connection() as conn:
                return await self.insert(task, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO tasks (conversation_id, turn_index, task_id, state)
                VALUES (%s, %s, %s, %s)
                RETURNING conversation_id, turn_index, task_id, state
                """,
                (
                    task.conversation_id, task.turn_index, task.task_id, task.state,
                ),
            )
            row = await cur.fetchone()

        return Task.model_validate(row)

    async def get_by_key(self, conversation_id: uuid.UUID, turn_index: int, conn=None) -> Task | None:
        if conn is None:
            async with pool.connection() as conn:
                return await self.get_by_key(conversation_id, turn_index, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT conversation_id, turn_index, task_id, state
                FROM tasks
                WHERE conversation_id = %s AND turn_index = %s
                """,
                (conversation_id, turn_index),
            )
            row = await cur.fetchone()

        if row is None:
            return None

        return Task.model_validate(row)

    async def delete_by_key(self, conversation_id: uuid.UUID, turn_index: int, conn=None) -> None:
        if conn is None:
            async with pool.connection() as conn:
                await self.delete_by_key(conversation_id, turn_index, conn=conn)
                return

        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM tasks
                WHERE conversation_id = %s AND turn_index = %s
                """,
                (conversation_id, turn_index),
            )

    async def delete_from_turn(self, conversation_id: uuid.UUID, turn_index: int, conn=None) -> list[uuid.UUID]:
        if conn is None:
            async with pool.connection() as conn:
                return await self.delete_from_turn(conversation_id, turn_index, conn=conn)

        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                DELETE FROM tasks
                WHERE conversation_id = %s AND turn_index >= %s
                RETURNING state
                """,
                (conversation_id, turn_index),
            )
            rows = await cur.fetchall()

        return [row["state"] for row in rows if row["state"] is not None]


'''
数据库建表语句：
CREATE TABLE tasks (
    conversation_id UUID NOT NULL,
    turn_index INTEGER NOT NULL,
    task_id UUID NOT NULL,
    state UUID,

    PRIMARY KEY (conversation_id, turn_index)
);
'''
