from app.core.config import settings
from psycopg_pool import AsyncConnectionPool

pool = AsyncConnectionPool(
    conninfo=settings.DATABASE_URL,
    min_size=2,
    max_size=10,
    timeout=5,
    open=False,
)

async def open_db():
    await pool.open()

async def close_db():
    await pool.close()