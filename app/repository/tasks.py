import uuid

from pydantic import BaseModel
from psycopg.rows import dict_row
from app.core.db import pool


class Task(BaseModel):
    task_id: uuid.UUID
    start_state: uuid.UUID
    end_state: uuid.UUID
    conversation_id: uuid.UUID
    turn_index: int

class TaskRepository:
    async def insert(self, task: Task) -> Task:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO tasks (task_id, start_state, end_state, conversation_id, turn_index)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING task_id, start_state, end_state, conversation_id, turn_index
                    """,
                    (
                        task.task_id, task.start_state, task.end_state,
                        task.conversation_id, task.turn_index,
                    ),
                )
                row = await cur.fetchone()

        return Task.model_validate(row)

    async def get_by_conversation_turn(self, conversation_id: uuid.UUID, turn_index: int) -> Task | None:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT task_id, start_state, end_state, conversation_id, turn_index
                    FROM tasks
                    WHERE conversation_id = %s AND turn_index = %s
                    """,
                    (conversation_id, turn_index),
                )
                row = await cur.fetchone()

        if row is None:
            return None

        return Task.model_validate(row)


'''
数据库建表语句：
CREATE TABLE tasks (
    task_id UUID NOT NULL,
    start_state UUID NOT NULL,
    end_state UUID NOT NULL,
    conversation_id UUID NOT NULL,
    turn_index INTEGER NOT NULL,

    PRIMARY KEY (conversation_id, turn_index)
);
'''
