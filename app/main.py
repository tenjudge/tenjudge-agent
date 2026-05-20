from fastapi import FastAPI

from contextlib import asynccontextmanager
from app.core.response import register_exception_handlers
from app.core.db import *

@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_db()
    yield
    await close_db()

app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)

'''
uv run uvicorn app.main:app --reload
'''
