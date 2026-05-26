from typing import Literal, Any, Type

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.config import settings

models = {
    "low": ChatOpenAI(
        model="deepseek/deepseek-v4-flash",
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        extra_body={"reasoning": {"effort": "none"}}
    ),
    "medium": ChatOpenAI(
        model="deepseek/deepseek-v4-pro",
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        extra_body={"reasoning": {"effort": "none"}}
    ),
    "high": ChatOpenAI(
        model="deepseek/deepseek-v4-pro",
        api_key=settings.OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    ),
}

# models = {
#     "low": ChatOpenAI(
#         model="deepseek-v4-flash",
#         api_key=settings.DEEPSEEK_API_KEY,
#         base_url="https://api.deepseek.com",
#     ),
#     "medium": ChatOpenAI(
#         model="deepseek-v4-pro",
#         api_key=settings.DEEPSEEK_API_KEY,
#         base_url="https://api.deepseek.com",
#         extra_body={"thinking": {"type": "disabled"}},
#     ),
#     "high": ChatOpenAI(
#         model="deepseek-v4-pro",
#         api_key=settings.DEEPSEEK_API_KEY,
#         base_url="https://api.deepseek.com",
#         extra_body={"thinking": {"type": "enabled"}},
#         reasoning_effort="high",
#     ),
# }

# models = {
#     "low": ChatOpenAI(
#         model="qwen-plus",
#         api_key=settings.DASHSCOPE_API_KEY,
#         base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
#     ),
#     "medium": ChatOpenAI(
#         model="qwen-plus",
#         api_key=settings.DASHSCOPE_API_KEY,
#         base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
#     ),
#     "high": ChatOpenAI(
#         model="qwen-plus",
#         api_key=settings.DASHSCOPE_API_KEY,
#         base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
#     ),
# }

class LLM:
    def __init__(self, level: Literal["low", "medium", "high"]):
        self.model = models[level]

    async def ainvoke(
        self,
        messages: list[Any],
        tools: list[Any] | None = None,
        structured_output: Type[BaseModel] | None = None,
    ):
        # 使用结构化输出会输出一个字典，result["raw"] : AIMessage，result["parsed"] : 结构化输出的结果
        model = self.model

        if tools and structured_output:
            raise ValueError("tools 和 structured_output 不建议在同一次调用里同时使用")

        if tools:
            model = model.bind_tools(tools)

        if structured_output:
            model = model.with_structured_output(structured_output, include_raw=True)

        model = model.with_retry(stop_after_attempt=3) # 失败重试

        return await model.ainvoke(messages)
