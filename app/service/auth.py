import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.core.response import BizException, Code


class CurrentUserIdVO(BaseModel):
    user_id: int | None = Field(default=None, alias="userId")


class CurrentUserIdResult(BaseModel):
    code: int
    data: CurrentUserIdVO | None = None


async def get_current_user_id(token: str) -> int | None:
    base_url = settings.TENJUDGE_SERVER_BASE_URL.rstrip("/")
    if not base_url:
        raise BizException(Code.SERVER_ERROR)

    try:
        # 向 tenjudge-server 查询当前 token 对应的用户 ID。
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{base_url}/auth/me/id",
                headers={"tenjudge-token": token},
            )
            response.raise_for_status()

        result = CurrentUserIdResult.model_validate(response.json())
    except httpx.HTTPError as exc:
        raise BizException(Code.SERVER_ERROR) from exc
    except (ValueError, ValidationError) as exc:
        raise BizException(Code.SERVER_ERROR) from exc

    if result.code != Code.SUCCESS.biz_code:
        raise BizException(Code.UNAUTHORIZED)

    return result.data.user_id if result.data else None
