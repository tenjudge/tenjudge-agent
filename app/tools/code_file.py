import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.context import CodeFile, CodeFileContext, get_code_file_contexts, normalize_code_content


CodeLanguage = Literal["cpp", "python", "else"]


class CreateCodeFileInput(BaseModel):
    description: str = Field(
        description="Required non-empty English description of the source file role.",
    )
    language: CodeLanguage = Field(
        description="Source language. Only cpp, python, and else are supported.",
    )
    content: str = Field(
        description="Required complete source code content. It must not be empty.",
    )


class ReplaceCodeFileContentInput(BaseModel):
    code_file_id: str = Field(
        description="The existing code file context id to update, such as code_file_1.",
    )
    old_string: str = Field(
        description=(
            "The exact string to replace. Empty old_string is only valid when the "
            "target file content is also empty."
        ),
    )
    new_string: str = Field(
        description="The replacement string. It may be empty when deleting matched code.",
    )
    replace_all: bool = Field(
        default=False,
        description="Whether to replace all occurrences. Defaults to false.",
    )
    description: str | None = Field(
        default=None,
        description="Optional new non-empty description. When provided, it fully replaces the old description.",
    )
    language: CodeLanguage | None = Field(
        default=None,
        description="Optional new language. When provided, it fully replaces the old language.",
    )


class ReplaceCodeFileContentAsNewInput(BaseModel):
    source_code_file_id: str = Field(
        description="The existing code file context id to use as the source, such as code_file_1.",
    )
    old_string: str = Field(
        description=(
            "The exact string to replace. Empty old_string is only valid when the "
            "source file content is also empty."
        ),
    )
    new_string: str = Field(
        description="The replacement string. It may be empty when deleting matched code.",
    )
    replace_all: bool = Field(
        default=False,
        description="Whether to replace all occurrences. Defaults to false.",
    )
    description: str = Field(
        description="Required non-empty English description for the new source file.",
    )
    language: CodeLanguage = Field(
        description="Required language for the new source file. Only cpp, python, and else are supported.",
    )


class OverwriteCodeFileInput(BaseModel):
    code_file_id: str = Field(
        description="The existing code file context id to overwrite, such as code_file_1.",
    )
    content: str = Field(
        description="Required complete replacement source code content. It must not be empty.",
    )
    description: str | None = Field(
        default=None,
        description="Optional new non-empty description. When provided, it fully replaces the old description.",
    )
    language: CodeLanguage | None = Field(
        default=None,
        description="Optional new language. When provided, it fully replaces the old language.",
    )


class UpdateCodeFileMetadataInput(BaseModel):
    code_file_id: str = Field(
        description="The existing code file context id to update, such as code_file_1.",
    )
    description: str | None = Field(
        default=None,
        description="Optional new non-empty description. At least one of description or language is required.",
    )
    language: CodeLanguage | None = Field(
        default=None,
        description="Optional new language. At least one of description or language is required.",
    )


@dataclass(frozen=True)
class ReplaceResult:
    success: bool
    message: str
    content: str
    replacement_count: int


PRIVATE_TOOL_CONTEXT_RULE = """Identifiers and metadata returned by this tool are private execution context.
Use them only for later tool calls; do not expose them in user-facing answers."""


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _format_available_ids(contexts: list[CodeFileContext]) -> str:
    available_ids = [context.id for context in contexts]
    return ", ".join(available_ids) if available_ids else "none"


def _copy_code_file_contexts(state: dict[str, Any]) -> list[CodeFileContext]:
    return [
        context.model_copy(deep=True)
        for context in get_code_file_contexts(state)
    ]


def _find_context_index(contexts: list[CodeFileContext], code_file_id: str) -> int | None:
    for index, context in enumerate(contexts):
        if context.id == code_file_id:
            return index
    return None


def _clean_required_text(value: str, field_name: str) -> tuple[str | None, str | None]:
    cleaned = value.strip()
    if not cleaned:
        return None, f"{field_name} must not be empty."
    return cleaned, None


def _clean_optional_text(value: str | None, field_name: str) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    cleaned = value.strip()
    if not cleaned:
        return None, f"{field_name} must not be empty when provided."
    return cleaned, None


def _validate_non_empty_content(content: str) -> str | None:
    if not content.strip():
        return "content must not be empty."
    return None


def _plural_replacement(count: int) -> str:
    return "replacement" if count == 1 else "replacements"


def _next_code_file_context_id(
        state: dict[str, Any],
        contexts: list[CodeFileContext],
) -> tuple[str, int]:
    # 1. 以 state 计数器为主，同时兼容旧数据里计数器偏小导致的新 id 冲突。
    state_count = int(state.get("code_file_cnt", 0) or 0)
    existing_count = 0
    for context in contexts:
        prefix = "code_file_"
        if context.id.startswith(prefix) and context.id[len(prefix):].isdigit():
            existing_count = max(existing_count, int(context.id[len(prefix):]))

    next_count = max(state_count, existing_count) + 1
    return f"code_file_{next_count}", next_count


def _file_payload(code_file_context: CodeFileContext) -> dict[str, Any]:
    return {
        "code_file_id": code_file_context.id,
        "description": code_file_context.file.description,
        "language": code_file_context.file.language,
        "content": code_file_context.file.content,
    }


def _make_command(
        runtime: ToolRuntime,
        payload: dict[str, Any],
        *,
        code_files: list[CodeFileContext] | None = None,
        code_file_cnt: int | None = None,
) -> Command:
    update: dict[str, Any] = {
        "messages": [
            ToolMessage(
                content=_dump_json(payload),
                tool_call_id=runtime.tool_call_id or "manual_tool_call",
            ),
        ],
    }
    if code_files is not None:
        update["code_files"] = code_files
    if code_file_cnt is not None:
        update["code_file_cnt"] = code_file_cnt
    return Command(update=update)


def _failure(
        runtime: ToolRuntime,
        message: str,
        **extra: Any,
) -> Command:
    return _make_command(runtime, {
        "success": False,
        "message": message,
        **extra,
    })


def _success(
        runtime: ToolRuntime,
        message: str,
        code_file_context: CodeFileContext,
        *,
        code_files: list[CodeFileContext],
        code_file_cnt: int | None = None,
        **extra: Any,
) -> Command:
    return _make_command(
        runtime,
        {
            "success": True,
            "message": message,
            **extra,
            **_file_payload(code_file_context),
        },
        code_files=code_files,
        code_file_cnt=code_file_cnt,
    )


def _not_found_failure(
        runtime: ToolRuntime,
        code_file_id: str,
        contexts: list[CodeFileContext],
        *,
        field_name: str = "code_file_id",
) -> Command:
    return _failure(
        runtime,
        (
            f"The requested internal {field_name} was not found. "
            f"Available internal code file handles: {_format_available_ids(contexts)}."
        ),
        **{field_name: code_file_id},
    )


def _apply_exact_replace(
        content: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool,
) -> ReplaceResult:
    normalized_content = normalize_code_content(content)
    normalized_old = normalize_code_content(old_string)
    normalized_new = normalize_code_content(new_string)

    # 1. 空 old_string 只允许表示“空文件的唯一插入点”，避免普通文件中无限位置插入。
    if normalized_old == "":
        if normalized_content != "":
            return ReplaceResult(
                success=False,
                message=(
                    "old_string is empty. Empty old_string is only allowed when "
                    "the target code file is also empty."
                ),
                content=normalized_content,
                replacement_count=0,
            )
        return ReplaceResult(
            success=True,
            message="Updated the empty code file.",
            content=normalized_new,
            replacement_count=1,
        )

    occurrence_count = normalized_content.count(normalized_old)
    if occurrence_count == 0:
        return ReplaceResult(
            success=False,
            message=(
                "old_string was not found in the target code file. No replacement was made. "
                "Use an exact substring from the current file content, or overwrite the file when making a large rewrite."
            ),
            content=normalized_content,
            replacement_count=0,
        )

    if occurrence_count > 1 and not replace_all:
        return ReplaceResult(
            success=False,
            message=(
                f"old_string appears {occurrence_count} times in the target code file. "
                "Because replace_all is false, no replacement was made. "
                "Provide a more unique old_string or set replace_all to true."
            ),
            content=normalized_content,
            replacement_count=0,
        )

    replacement_limit = -1 if replace_all else 1
    updated_content = normalized_content.replace(
        normalized_old,
        normalized_new,
        replacement_limit,
    )
    replacement_count = occurrence_count if replace_all else 1
    return ReplaceResult(
        success=True,
        message=(
            f"Updated the code file with {replacement_count} "
            f"{_plural_replacement(replacement_count)}."
        ),
        content=updated_content,
        replacement_count=replacement_count,
    )


def _resolve_metadata(
        *,
        current_description: str,
        current_language: CodeLanguage,
        description: str | None,
        language: CodeLanguage | None,
) -> tuple[str | None, CodeLanguage | None, str | None]:
    cleaned_description, error = _clean_optional_text(description, "description")
    if error:
        return None, None, error

    return (
        cleaned_description if cleaned_description is not None else current_description,
        language if language is not None else current_language,
        None,
    )


CREATE_CODE_FILE_DESCRIPTION = """Create a new code file in the current agent state.
Use this tool when you have complete source code that should be stored under a new internal code-file handle.
description, language, and content are required. content must not be empty.
Do not call multiple code-file write tools for the same target file at the same time. Wait for the previous tool result, then continue from the returned full content.
""" + PRIVATE_TOOL_CONTEXT_RULE


@tool(
    args_schema=CreateCodeFileInput,
    description=CREATE_CODE_FILE_DESCRIPTION,
)
async def create_code_file(
        description: str,
        language: CodeLanguage,
        content: str,
        runtime: ToolRuntime,
) -> Command:
    """Create a new non-empty code file in state."""
    state = runtime.state
    contexts = _copy_code_file_contexts(state)

    cleaned_description, error = _clean_required_text(description, "description")
    if error:
        return _failure(runtime, error)

    normalized_content = normalize_code_content(content)
    if error := _validate_non_empty_content(normalized_content):
        return _failure(runtime, error)

    code_file_id, code_file_cnt = _next_code_file_context_id(state, contexts)
    code_file_context = CodeFileContext(
        id=code_file_id,
        file=CodeFile(
            description=cleaned_description,
            language=language,
            content=normalized_content,
        ),
    )
    contexts.append(code_file_context)

    return _success(
        runtime,
        "Created a new code file.",
        code_file_context,
        code_files=contexts,
        code_file_cnt=code_file_cnt,
    )


REPLACE_CODE_FILE_CONTENT_DESCRIPTION = """Replace an exact string inside an existing state code file.
Use this tool for precise edits to one existing internal code-file handle. The replacement updates the same internal handle.
old_string is an exact substring. Empty old_string is valid only when the current file content is empty.
If replace_all is false and old_string appears more than once, the tool returns success=false and makes no change.
Optional description and language fully replace the old metadata when provided.
The tool returns the complete updated file content on success.
Do not call multiple code-file write tools for the same target file at the same time. Wait for the previous tool result, then continue from the returned full content.
""" + PRIVATE_TOOL_CONTEXT_RULE


@tool(
    args_schema=ReplaceCodeFileContentInput,
    description=REPLACE_CODE_FILE_CONTENT_DESCRIPTION,
)
async def replace_code_file_content(
        code_file_id: str,
        old_string: str,
        new_string: str,
        runtime: ToolRuntime,
        replace_all: bool = False,
        description: str | None = None,
        language: CodeLanguage | None = None,
) -> Command:
    """Replace exact source text inside an existing state code file."""
    state = runtime.state
    contexts = _copy_code_file_contexts(state)
    index = _find_context_index(contexts, code_file_id)
    if index is None:
        return _not_found_failure(runtime, code_file_id, contexts)

    context = contexts[index]
    new_description, new_language, error = _resolve_metadata(
        current_description=context.file.description,
        current_language=context.file.language,
        description=description,
        language=language,
    )
    if error:
        return _failure(runtime, error, code_file_id=code_file_id)

    replace_result = _apply_exact_replace(
        context.file.content,
        old_string,
        new_string,
        replace_all=replace_all,
    )
    if not replace_result.success:
        return _failure(runtime, replace_result.message, code_file_id=code_file_id)

    updated_context = CodeFileContext(
        id=context.id,
        file=CodeFile(
            description=new_description,
            language=new_language,
            content=replace_result.content,
        ),
    )
    contexts[index] = updated_context

    return _success(
        runtime,
        replace_result.message,
        updated_context,
        code_files=contexts,
        replacement_count=replace_result.replacement_count,
    )


REPLACE_CODE_FILE_CONTENT_AS_NEW_DESCRIPTION = """Create a new code file by applying an exact string replacement to an existing state code file.
Use this tool when the original internal code file should remain unchanged and the revised source should be saved under a new internal code-file handle.
description and language are required for the new file.
old_string is an exact substring. Empty old_string is valid only when the source file content is empty.
If replace_all is false and old_string appears more than once, the tool returns success=false and makes no change.
The tool returns the complete new file content on success.
Do not call multiple code-file write tools for the same source or target file at the same time. Wait for the previous tool result, then continue from the returned full content.
""" + PRIVATE_TOOL_CONTEXT_RULE


@tool(
    args_schema=ReplaceCodeFileContentAsNewInput,
    description=REPLACE_CODE_FILE_CONTENT_AS_NEW_DESCRIPTION,
)
async def replace_code_file_content_as_new(
        source_code_file_id: str,
        old_string: str,
        new_string: str,
        description: str,
        language: CodeLanguage,
        runtime: ToolRuntime,
        replace_all: bool = False,
) -> Command:
    """Create a new state code file from an exact replacement on another code file."""
    state = runtime.state
    contexts = _copy_code_file_contexts(state)
    source_index = _find_context_index(contexts, source_code_file_id)
    if source_index is None:
        return _not_found_failure(
            runtime,
            source_code_file_id,
            contexts,
            field_name="source_code_file_id",
        )

    cleaned_description, error = _clean_required_text(description, "description")
    if error:
        return _failure(runtime, error, source_code_file_id=source_code_file_id)

    replace_result = _apply_exact_replace(
        contexts[source_index].file.content,
        old_string,
        new_string,
        replace_all=replace_all,
    )
    if not replace_result.success:
        return _failure(runtime, replace_result.message, source_code_file_id=source_code_file_id)

    code_file_id, code_file_cnt = _next_code_file_context_id(state, contexts)
    code_file_context = CodeFileContext(
        id=code_file_id,
        file=CodeFile(
            description=cleaned_description,
            language=language,
            content=replace_result.content,
        ),
    )
    contexts.append(code_file_context)

    return _success(
        runtime,
        (
            "Created a new revised code file with "
            f"{replace_result.replacement_count} "
            f"{_plural_replacement(replace_result.replacement_count)}."
        ),
        code_file_context,
        code_files=contexts,
        code_file_cnt=code_file_cnt,
        source_code_file_id=source_code_file_id,
        replacement_count=replace_result.replacement_count,
    )


OVERWRITE_CODE_FILE_DESCRIPTION = """Overwrite an existing state code file with complete source code.
Use this tool for large rewrites or when exact replacement is not practical.
content is required and must not be empty. Optional description and language fully replace the old metadata when provided.
The tool returns the complete updated file content on success.
Do not call multiple code-file write tools for the same target file at the same time. Wait for the previous tool result, then continue from the returned full content.
""" + PRIVATE_TOOL_CONTEXT_RULE


@tool(
    args_schema=OverwriteCodeFileInput,
    description=OVERWRITE_CODE_FILE_DESCRIPTION,
)
async def overwrite_code_file(
        code_file_id: str,
        content: str,
        runtime: ToolRuntime,
        description: str | None = None,
        language: CodeLanguage | None = None,
) -> Command:
    """Overwrite an existing state code file with non-empty complete content."""
    state = runtime.state
    contexts = _copy_code_file_contexts(state)
    index = _find_context_index(contexts, code_file_id)
    if index is None:
        return _not_found_failure(runtime, code_file_id, contexts)

    normalized_content = normalize_code_content(content)
    if error := _validate_non_empty_content(normalized_content):
        return _failure(runtime, error, code_file_id=code_file_id)

    context = contexts[index]
    new_description, new_language, error = _resolve_metadata(
        current_description=context.file.description,
        current_language=context.file.language,
        description=description,
        language=language,
    )
    if error:
        return _failure(runtime, error, code_file_id=code_file_id)

    updated_context = CodeFileContext(
        id=context.id,
        file=CodeFile(
            description=new_description,
            language=new_language,
            content=normalized_content,
        ),
    )
    contexts[index] = updated_context

    return _success(
        runtime,
        "Overwrote the code file.",
        updated_context,
        code_files=contexts,
    )


UPDATE_CODE_FILE_METADATA_DESCRIPTION = """Update metadata for an existing state code file without changing its source content.
Use this tool when only description or language should change.
At least one of description or language is required. description must not be empty when provided.
The tool returns the complete file content on success.
Do not call multiple code-file write tools for the same target file at the same time. Wait for the previous tool result, then continue from the returned full content.
""" + PRIVATE_TOOL_CONTEXT_RULE


@tool(
    args_schema=UpdateCodeFileMetadataInput,
    description=UPDATE_CODE_FILE_METADATA_DESCRIPTION,
)
async def update_code_file_metadata(
        code_file_id: str,
        runtime: ToolRuntime,
        description: str | None = None,
        language: CodeLanguage | None = None,
) -> Command:
    """Update description or language for an existing state code file."""
    state = runtime.state
    contexts = _copy_code_file_contexts(state)
    index = _find_context_index(contexts, code_file_id)
    if index is None:
        return _not_found_failure(runtime, code_file_id, contexts)

    if description is None and language is None:
        return _failure(
            runtime,
            "At least one of description or language is required.",
            code_file_id=code_file_id,
        )

    context = contexts[index]
    new_description, new_language, error = _resolve_metadata(
        current_description=context.file.description,
        current_language=context.file.language,
        description=description,
        language=language,
    )
    if error:
        return _failure(runtime, error, code_file_id=code_file_id)

    updated_context = CodeFileContext(
        id=context.id,
        file=CodeFile(
            description=new_description,
            language=new_language,
            content=normalize_code_content(context.file.content),
        ),
    )
    contexts[index] = updated_context

    return _success(
        runtime,
        "Updated the code file metadata.",
        updated_context,
        code_files=contexts,
    )


CODE_FILE_TOOLS = [
    create_code_file,
    replace_code_file_content,
    replace_code_file_content_as_new,
    overwrite_code_file,
    update_code_file_metadata,
]
