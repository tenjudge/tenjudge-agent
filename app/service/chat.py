import uuid
import operator

from pydantic import BaseModel, Field

from langchain.messages import AnyMessage
from typing_extensions import TypedDict, Annotated
from typing import List, Literal, Union

# ===== Request ==========================================================
class CodeAttachment(BaseModel):
    type: Literal["code"]
    content: str

class SubmissionAttachment(BaseModel):
    type: Literal["submission"]
    submission_id: int

class ProblemAttachment(BaseModel):
    type: Literal["problem"]
    problem_id: int

Attachment = Annotated[
    Union[
        CodeAttachment,
        SubmissionAttachment,
        ProblemAttachment,
    ],
    Field(discriminator="type")
]

class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str
    turn_index: int
    attachments: List[Attachment] = Field(default_factory=list)

# ===== State ==========================================================

class CodeFile(BaseModel):
    id: int
    description: str
    language: Literal["cpp", "python", "else"]
    content: str

class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    files: List[CodeFile]
    token: str
    user_id: int


async def handle_chat(request: ChatRequest, token: str, user_id: int):
    # token 已在路由层校验，这里直接使用可信的 user_id。

    if request.conversation_id:
        # TODO
        # 
        pass
    else:
        # TODO 新建一个conversation行
        pass


    for attachment in request.attachments:
        if attachment.type == "code":
            print("用户上传代码")
            print(attachment.content)

        elif attachment.type == "submission":
            print("用户选择提交")
            print(attachment.submission_id)

        elif attachment.type == "problem":
            print("用户选择题目")
            print(attachment.problem_id)
