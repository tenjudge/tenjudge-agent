
from fastapi import APIRouter

from app.core.response import Result
from app.service.chat import ChatRequest, handle_chat

router = APIRouter()


@router.post("/agent/chat")
async def chat(request: ChatRequest):
    await handle_chat(request)


    return Result.success()