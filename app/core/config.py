from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

# 应用运行配置，支持从环境变量和 .env 文件加载。
class Settings(BaseSettings):

    OPENROUTER_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""
    DATABASE_URL: str = ""
    TENJUDGE_SERVER_BASE_URL: str = ""
    REDIS_URL: str = ""
    REDIS_STREAM_TTL_SECONDS: int = 3600
    REDIS_STREAM_READ_BLOCK_MS: int = 15000
    REDIS_STREAM_READ_COUNT: int = 10
    AGENT_MAX_REACT_ROUNDS: int = 8
    AGENT_REACT_ROUND_WARNING_REMAINING: int = 2
    AGENT_DB_TOOL_DATABASE_URL: str = ""
    RAG_SKILL_DIR: str = ""
    RAG_KNOWLEDGE_DIR: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

# 获取全局配置对象，并用缓存避免重复解析环境变量。
@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
