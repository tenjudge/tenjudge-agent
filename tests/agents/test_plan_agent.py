import pytest
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agents import plan_agent
from app.agents.plan_agent import (
    Plan,
    Step,
    ToolSuggestion,
    format_plan_for_message,
    make_plan,
)


class FileReadResult(BaseModel):
    path: str = Field(description="The file path that was read.")
    content: str = Field(description="The returned file content.")


def test_tool_input_schema_uses_parse_docstring_when_present():
    @tool(parse_docstring=True)
    def read_file(path: str, limit: int = 200) -> str:
        """Read a source file.

        Args:
            path: File path to read.
            limit: Max lines to return.
        """
        return "content"

    schema = plan_agent._get_tool_input_schema(read_file)

    assert schema["properties"]["path"]["description"] == "File path to read."
    assert schema["properties"]["limit"]["description"] == "Max lines to return."


def test_tool_prompt_keeps_docstring_when_parse_docstring_is_absent():
    @tool
    def read_file(path: str) -> str:
        """Read a source file.

        Args:
            path: File path to read.
        """
        return "content"

    tools_prompt = plan_agent._build_available_tools_prompt([read_file])

    assert "read_file" in tools_prompt
    assert "File path to read." in tools_prompt


def test_tool_output_schema_uses_basemodel_return_annotation():
    @tool
    def read_file(path: str) -> FileReadResult:
        """Read a source file."""
        return FileReadResult(path=path, content="content")

    schema = plan_agent._get_tool_output_schema(read_file)

    assert schema["properties"]["path"]["description"] == "The file path that was read."
    assert schema["properties"]["content"]["description"] == "The returned file content."


def test_format_plan_for_message_returns_internal_plan_text():
    plan = Plan(
        summary="Inspect the current context.",
        steps=[
            Step(
                description="Read the relevant source file.",
                tool_suggestions=[
                    ToolSuggestion(
                        name="read_file",
                        reason="It can inspect the file content.",
                    )
                ],
            )
        ],
    )

    message = format_plan_for_message(plan)

    assert message.startswith("[Internal plan]")
    assert "It is not a user-facing answer." in message
    assert '"summary": "Inspect the current context."' in message
    assert '"tool_suggestions"' in message


@pytest.mark.asyncio
async def test_make_plan_retries_unavailable_tool_suggestion(monkeypatch):
    @tool
    def read_file(path: str) -> str:
        """Read a source file."""
        return "content"

    calls: list[str | None] = []

    async def fake_invoke_plan_model(*args, retry_note=None, **kwargs):
        calls.append(retry_note)
        if len(calls) == 1:
            return Plan(
                summary="Inspect the context.",
                steps=[
                    Step(
                        description="Use an unavailable tool first.",
                        tool_suggestions=[
                            ToolSuggestion(
                                name="missing_tool",
                                reason="This should trigger a retry.",
                            )
                        ],
                    )
                ],
            )
        return Plan(
            summary="Inspect the context.",
            steps=[
                Step(
                    description="Read the relevant file.",
                    tool_suggestions=[
                        ToolSuggestion(
                            name="read_file",
                            reason="It can inspect the source file.",
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(
        plan_agent,
        "_invoke_plan_model",
        fake_invoke_plan_model,
    )

    plan = await make_plan(messages=[], available_tools=[read_file])

    assert plan.steps[0].tool_suggestions[0].name == "read_file"
    assert calls[0] is None
    assert "missing_tool" in calls[1]
