from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.response import register_exception_handlers
from app.core.db import *
from app.core.redis import close_redis
from app.router.chat import router as chat_router
from app.router.conversation import router as conversation_router

LOCAL_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_db()
    yield
    await close_redis()
    await close_db()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)

app.include_router(chat_router)
app.include_router(conversation_router)

'''
uv run uvicorn app.main:app --reload
'''
