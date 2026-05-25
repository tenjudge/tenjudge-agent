import json
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.context import Submission, SubmissionContext
from app.agents.orchestrator import (
    _current_turn_react_round_count,
    get_init_state,
    route_after_agent,
    state_from_dict,
    state_to_dict,
)
from app.core.config import settings


def test_state_to_dict_serializes_submission_datetime_as_json():
    state = get_init_state()
    state["submission_cnt"] = 1
    state["submissions"].append(
        SubmissionContext(
            id="submission_1",
            submission=Submission(
                id=1001,
                problemId=2002,
                problemName="A + B",
                submitTime=datetime(2026, 5, 24, 12, 30, 45),
                language="python",
                status="Accepted",
                code="print(input())",
            ),
        )
    )

    dumped = state_to_dict(state)

    json.dumps(dumped)
    assert dumped["submissions"][0]["submission"]["submit_time"] == "2026-05-24T12:30:45"

    restored = state_from_dict(dumped)
    assert restored["submissions"][0].submission.submit_time == datetime(2026, 5, 24, 12, 30, 45)


def test_current_turn_react_round_count_starts_after_latest_human_message(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_MAX_REACT_ROUNDS", 2)

    state = get_init_state()
    state["messages"].extend([
        HumanMessage(content="previous turn"),
        AIMessage(content="old agent round"),
        HumanMessage(content="current turn"),
        SystemMessage(content="[Internal plan]\n\nUse tools if needed."),
        AIMessage(content="first round"),
        ToolMessage(content="first result", tool_call_id="call_1"),
        ToolMessage(content="second result", tool_call_id="call_2"),
    ])

    assert _current_turn_react_round_count(state) == 1

    state["messages"].extend([
        AIMessage(content="second round"),
        ToolMessage(content="third result", tool_call_id="call_3"),
    ])

    assert _current_turn_react_round_count(state) == 2


def test_route_after_agent_routes_by_tool_calls():
    state = get_init_state()
    state["messages"].append(AIMessage(content="answer"))
    assert route_after_agent(state) == "end"

    state["messages"][-1] = AIMessage(
        content="",
        tool_calls=[{
            "name": "search",
            "args": {},
            "id": "call_1",
        }],
    )
    assert route_after_agent(state) == "tools"
