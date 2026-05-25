from redis.asyncio import Redis

from app.core.config import settings


# 全局 Redis 客户端：Redis 连接会在第一次命令执行时按需建立。
redis_client: Redis = Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
)


async def close_redis():
    await redis_client.aclose()
