import json
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from typing_extensions import Annotated


TIMEZONE = ZoneInfo("Asia/Shanghai")


@tool
async def get_current_time() -> str:
    """Get the current time in Asia/Shanghai."""
    now = datetime.now(TIMEZONE)
    return json.dumps({
        "datetime": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
    }, ensure_ascii=False)


@tool
async def get_current_user_id(state: Annotated[dict, InjectedState]) -> str:
    """Get the authenticated TenJudge user id for the current chat."""
    return json.dumps({
        "user_id": state.get("user_id", 0),
    }, ensure_ascii=False)
