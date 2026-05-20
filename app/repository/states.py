import uuid
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel
from psycopg.rows import dict_row
from app.core.db import pool

class State(BaseModel):
    id: uuid.UUID
    state: dict[str, Any]

class StateRepository:
    async def insert(self, state: State) -> State:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO states (id, state)
                    VALUES (%s, %s)
                    RETURNING id, state
                    """,
                    (state.id, Jsonb(state.state)),
                )
                row = await cur.fetchone()

        return State.model_validate(row)

    async def select(self, state_id: uuid.UUID) -> State | None:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id, state
                    FROM states
                    WHERE id = %s
                    """,
                    (state_id,),
                )
                row = await cur.fetchone()

        if row is None:
            return None

        return State.model_validate(row)


'''
数据库建表语句：
CREATE TABLE states (
    id UUID PRIMARY KEY,
    state JSONB NOT NULL
);
'''
