import json
import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agents.context import CodeFile, CodeFileContext
from app.agents.orchestrator import AGENT_TOOLS, State, get_init_state
from app.tools import code_file


def make_runtime(state: dict, tool_call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(state=state, tool_call_id=tool_call_id)


def command_payload(command) -> dict:
    tool_message = command.update["messages"][0]
    assert isinstance(tool_message, ToolMessage)
    return json.loads(tool_message.content)


def assert_message_has_no_internal_code_file_id(payload: dict) -> None:
    assert not re.search(r"\b(?:code_file|problem|submission)_\d+\b", payload["message"])
    assert "code_file_id" not in payload["message"]
    assert "source_code_file_id" not in payload["message"]


def make_state(content: str = "int main() { return 0; }") -> dict:
    state = get_init_state()
    state["code_file_cnt"] = 1
    state["code_files"].append(
        CodeFileContext(
            id="code_file_1",
            file=CodeFile(
                description="Original C++ solution.",
                language="cpp",
                content=content,
            ),
        )
    )
    return state


@pytest.mark.asyncio
async def test_create_code_file_updates_state_and_normalizes_newlines():
    state = get_init_state()

    command = await code_file.create_code_file.coroutine(
        description="Generated Python solution.",
        language="python",
        content="print(1)\r\nprint(2)\r\n",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    assert payload["success"] is True
    assert payload["message"] == "Created a new code file."
    assert_message_has_no_internal_code_file_id(payload)
    assert payload["code_file_id"] == "code_file_1"
    assert payload["content"] == "print(1)\nprint(2)\n"
    assert command.update["code_file_cnt"] == 1
    assert command.update["code_files"][0].file.content == "print(1)\nprint(2)\n"


@pytest.mark.asyncio
async def test_create_code_file_rejects_empty_content():
    state = get_init_state()

    command = await code_file.create_code_file.coroutine(
        description="Empty file.",
        language="cpp",
        content="\n\t ",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    assert payload == {
        "success": False,
        "message": "content must not be empty.",
    }
    assert "code_files" not in command.update


@pytest.mark.asyncio
async def test_replace_code_file_content_updates_existing_file_and_metadata():
    state = make_state("int a = 1;\r\nreturn a;\r\n")

    command = await code_file.replace_code_file_content.coroutine(
        code_file_id="code_file_1",
        old_string="int a = 1;\nreturn a;",
        new_string="int a = 2;\nreturn a;",
        replace_all=False,
        description="Updated C++ solution.",
        language="cpp",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    assert payload["success"] is True
    assert payload["message"] == "Updated the code file with 1 replacement."
    assert_message_has_no_internal_code_file_id(payload)
    assert payload["replacement_count"] == 1
    assert payload["description"] == "Updated C++ solution."
    assert payload["content"] == "int a = 2;\nreturn a;\n"
    assert command.update["code_files"][0].file.content == "int a = 2;\nreturn a;\n"


@pytest.mark.asyncio
async def test_replace_code_file_content_rejects_duplicate_match_without_replace_all():
    state = make_state("x();\nx();\n")

    command = await code_file.replace_code_file_content.coroutine(
        code_file_id="code_file_1",
        old_string="x();",
        new_string="y();",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    assert payload["success"] is False
    assert payload["code_file_id"] == "code_file_1"
    assert "appears 2 times" in payload["message"]
    assert_message_has_no_internal_code_file_id(payload)
    assert "code_files" not in command.update
    assert state["code_files"][0].file.content == "x();\nx();\n"


@pytest.mark.asyncio
async def test_replace_code_file_content_allows_empty_old_string_for_empty_file():
    state = make_state("")

    command = await code_file.replace_code_file_content.coroutine(
        code_file_id="code_file_1",
        old_string="",
        new_string="print(1)\n",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    assert payload["success"] is True
    assert payload["message"] == "Updated the empty code file."
    assert_message_has_no_internal_code_file_id(payload)
    assert payload["content"] == "print(1)\n"
    assert command.update["code_files"][0].file.content == "print(1)\n"


@pytest.mark.asyncio
async def test_replace_code_file_content_rejects_empty_old_string_for_non_empty_file():
    state = make_state("print(0)\n")

    command = await code_file.replace_code_file_content.coroutine(
        code_file_id="code_file_1",
        old_string="",
        new_string="print(1)\n",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    assert payload["success"] is False
    assert "only allowed when the target code file is also empty" in payload["message"]
    assert_message_has_no_internal_code_file_id(payload)
    assert "code_files" not in command.update


@pytest.mark.asyncio
async def test_replace_code_file_content_as_new_preserves_source_and_advances_counter():
    state = make_state("print(0)\n")

    command = await code_file.replace_code_file_content_as_new.coroutine(
        source_code_file_id="code_file_1",
        old_string="print(0)",
        new_string="print(1)",
        description="Revised Python solution.",
        language="python",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    updated_files = command.update["code_files"]
    assert payload["success"] is True
    assert payload["message"] == "Created a new revised code file with 1 replacement."
    assert_message_has_no_internal_code_file_id(payload)
    assert payload["source_code_file_id"] == "code_file_1"
    assert payload["code_file_id"] == "code_file_2"
    assert payload["content"] == "print(1)\n"
    assert command.update["code_file_cnt"] == 2
    assert updated_files[0].id == "code_file_1"
    assert updated_files[0].file.content == "print(0)\n"
    assert updated_files[1].id == "code_file_2"


@pytest.mark.asyncio
async def test_overwrite_code_file_rejects_empty_content():
    state = make_state("print(0)\n")

    command = await code_file.overwrite_code_file.coroutine(
        code_file_id="code_file_1",
        content=" \n",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    assert payload == {
        "success": False,
        "message": "content must not be empty.",
        "code_file_id": "code_file_1",
    }
    assert "code_files" not in command.update


@pytest.mark.asyncio
async def test_overwrite_code_file_success_message_hides_internal_id():
    state = make_state("print(0)\n")

    command = await code_file.overwrite_code_file.coroutine(
        code_file_id="code_file_1",
        content="print(1)\n",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    assert payload["success"] is True
    assert payload["message"] == "Overwrote the code file."
    assert_message_has_no_internal_code_file_id(payload)
    assert command.update["code_files"][0].file.content == "print(1)\n"


@pytest.mark.asyncio
async def test_update_code_file_metadata_keeps_content_and_returns_full_file():
    state = make_state("print(0)\r\n")

    command = await code_file.update_code_file_metadata.coroutine(
        code_file_id="code_file_1",
        description="Python helper snippet.",
        language="python",
        runtime=make_runtime(state),
    )

    payload = command_payload(command)
    updated_file = command.update["code_files"][0]
    assert payload["success"] is True
    assert payload["message"] == "Updated the code file metadata."
    assert_message_has_no_internal_code_file_id(payload)
    assert payload["description"] == "Python helper snippet."
    assert payload["language"] == "python"
    assert payload["content"] == "print(0)\n"
    assert updated_file.file.content == "print(0)\n"


@pytest.mark.asyncio
async def test_code_file_command_tool_updates_state_through_tool_node():
    workflow = StateGraph(State)
    workflow.add_node("tools_node", ToolNode([code_file.replace_code_file_content]))
    workflow.add_edge(START, "tools_node")
    workflow.add_edge("tools_node", END)
    graph = workflow.compile()

    state = make_state("print(0)\n")
    state["messages"].append(
        AIMessage(
            content="",
            tool_calls=[{
                "name": "replace_code_file_content",
                "args": {
                    "code_file_id": "code_file_1",
                    "old_string": "print(0)",
                    "new_string": "print(1)",
                },
                "id": "call_1",
                "type": "tool_call",
            }],
        )
    )

    result = await graph.ainvoke(state)

    payload = json.loads(result["messages"][-1].content)
    assert payload["success"] is True
    assert_message_has_no_internal_code_file_id(payload)
    assert result["code_files"][0].file.content == "print(1)\n"


def test_code_file_tools_are_registered_for_main_agent():
    for tool in code_file.CODE_FILE_TOOLS:
        assert tool in AGENT_TOOLS
        assert "runtime" not in tool.args
        assert "private execution context" in tool.description
