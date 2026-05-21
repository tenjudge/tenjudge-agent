from typing import Any

from langchain.messages import AnyMessage
from langchain_core.messages import messages_from_dict, messages_to_dict
from typing_extensions import TypedDict, Annotated
import operator

class State(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]

def get_init_state() -> State:
    return {
        'messages': [],
    }

def state_to_dict(state: State) -> dict[str, Any]:
    return {
        **state,
        "messages": messages_to_dict(state["messages"]),
    }

def state_from_dict(state: dict[str, Any]) -> State:
    return {
        **state,
        "messages": messages_from_dict(state["messages"]),
    }
