from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CodeFile(BaseModel):
    description: str
    language: Literal["cpp", "python", "else"]
    content: str


class CodeFileContext(BaseModel):
    id: str
    file: CodeFile


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
