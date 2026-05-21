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
    async def insert(self, state: State, conn=None) -> State:
        if conn is None:
            async with pool.connection() as conn:
                return await self.insert(state, conn=conn)

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

    async def select(self, state_id: uuid.UUID, conn=None) -> State | None:
        if conn is None:
            async with pool.connection() as conn:
                return await self.select(state_id, conn=conn)

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

    async def delete_by_ids(self, state_ids: list[uuid.UUID], conn=None) -> None:
        if not state_ids:
            return

        if conn is None:
            async with pool.connection() as conn:
                await self.delete_by_ids(state_ids, conn=conn)
                return

        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM states
                WHERE id = ANY(%s::uuid[])
                """,
                (state_ids,),
            )


'''
数据库建表语句：
CREATE TABLE states (
    id UUID PRIMARY KEY,
    state JSONB NOT NULL
);
'''
