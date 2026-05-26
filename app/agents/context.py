from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CodeFile(BaseModel):
    description: str
    language: Literal["cpp", "python", "else"]
    content: str


class CodeFileContext(BaseModel):
    id: str
    file: CodeFile


def normalize_code_content(content: str) -> str:
    # 1. state 内统一使用 LF，避免模型参数和源码物理换行不同导致精确替换失败。
    return content.replace("\r\n", "\n").replace("\r", "\n")


def get_code_file_contexts(state: dict[str, Any]) -> list[CodeFileContext]:
    contexts: list[CodeFileContext] = []
    for item in state.get("code_files", []):
        if isinstance(item, CodeFileContext):
            contexts.append(item)
        else:
            contexts.append(CodeFileContext.model_validate(item))
    return contexts


def find_code_file_context(state: dict[str, Any], code_file_id: str) -> CodeFileContext | None:
    for code_file_context in get_code_file_contexts(state):
        if code_file_context.id == code_file_id:
            return code_file_context
    return None


def format_available_code_file_ids(state: dict[str, Any]) -> str:
    available_ids = [context.id for context in get_code_file_contexts(state)]
    return ", ".join(available_ids) if available_ids else "none"


class Problem(BaseModel):
    problem_id: int = Field(alias="id")
    author_id: int = Field(alias="authorId")
    visibility: str
    checker: str
    time_limit: int = Field(alias="timeLimit")
    memory_limit: int = Field(alias="memoryLimit")
    name: str
    statement: str
    solution: str | None = None
    difficulty: int | None = None
    version: int
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ProblemContext(BaseModel):
    id: str
    problem: Problem


class SubmissionDetail(BaseModel):
    test_case_id: int = Field(alias="testCaseId")
    status: str
    time: int | None = None
    memory: int | None = None
    info: str | None = None
    input: str | None = None
    output: str | None = None
    answer: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class Submission(BaseModel):
    submission_id: int = Field(alias="id")
    problem_id: int = Field(alias="problemId")
    problem_name: str = Field(alias="problemName")
    submit_time: datetime = Field(alias="submitTime")
    language: str
    status: str
    time: int | None = None
    memory: int | None = None
    info: str | None = None
    code: str
    details: list[SubmissionDetail] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class SubmissionContext(BaseModel):
    id: str
    submission: Submission
