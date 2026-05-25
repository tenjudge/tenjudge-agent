import pytest

from app.agents import title_agent
from app.agents.title_agent import TitleResult, summarize_title


@pytest.mark.asyncio
async def test_summarize_title_uses_structured_output_and_cleans_title(monkeypatch):
    calls = {}

    class FakeLLM:
        def __init__(self, level):
            calls["level"] = level

        async def ainvoke(self, messages, structured_output=None):
            calls["messages"] = messages
            calls["structured_output"] = structured_output
            return {"parsed": TitleResult(title="“最短路代码调试？”")}

    monkeypatch.setattr(title_agent, "LLM", FakeLLM)

    title = await summarize_title("为什么这份最短路代码 WA？")

    assert title == "最短路代码调试"
    assert calls["level"] == "low"
    assert calls["structured_output"] is TitleResult
    assert "为什么这份最短路代码 WA？" in calls["messages"][-1].content


@pytest.mark.asyncio
async def test_summarize_title_rejects_invalid_structured_output(monkeypatch):
    class FakeLLM:
        def __init__(self, level):
            pass

        async def ainvoke(self, messages, structured_output=None):
            return {"parsed": None}

    monkeypatch.setattr(title_agent, "LLM", FakeLLM)

    with pytest.raises(ValueError, match="有效结构化结果"):
        await summarize_title("帮我看一下这题")
