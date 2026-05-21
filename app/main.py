from fastapi import FastAPI

from contextlib import asynccontextmanager
from app.core.response import register_exception_handlers
from app.core.db import *
from app.router.chat import router as chat_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_db()
    yield
    await close_db()

app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)

app.include_router(chat_router)

'''
uv run uvicorn app.main:app --reload
'''
