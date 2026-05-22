# AGENTS.md

## Project

This repository is the agent part of an OJ project.

## Confirmed Runtime

- Project name: `tenjudge-agent`
- Python requirement: `>=3.14`
- Dependency manager files present: `pyproject.toml`, `uv.lock`
- Unit tests use `pytest`; run them with `uv run pytest`.
- Main FastAPI app entrypoint: `app.main:app`
- Development command recorded in source:

```bash
uv run uvicorn app.main:app --reload
```

## Confirmed Application Structure

- `app/main.py` creates the FastAPI application.
- `app/router/chat.py` defines the `/agent/chat` route and owns the chat request/response DTO models.
- `app/service/chat.py` contains chat service code.
- `app/service/tenjudge_server.py` contains outbound HTTP calls to the TenJudge server, including current-user, problem, and submission lookup.
- `app/agents/context.py` contains agent context models such as `CodeFile`, `CodeFileContext`, `Problem`, `ProblemContext`, `Submission`, `SubmissionDetail`, and `SubmissionContext`.
- `app/agents/orchestrator.py` owns the chat agent `State` TypedDict definition.
- `app/repository/messages.py` contains message persistence code; messages use `(conversation_id, turn_index, role)` as the primary key and expose `get_by_key`, `delete_by_key`, and `delete_from_turn`.
- `app/repository/tasks.py` contains task persistence code; tasks use `(conversation_id, turn_index)` as the primary key and expose `get_by_key`, `delete_by_key`, and `delete_from_turn`.
- `app/repository/states.py` contains LangGraph state persistence code and exposes `delete_by_ids` for deleting task-owned state snapshots.
- `app/core/response.py` contains unified response and exception handling code.
- `app/core/config.py` contains configuration code.
- `app/core/db.py` contains database lifecycle code.

## Confirmed Architecture Plan

- Chat submission is planned as two phases: a `POST` request submits chat input, and a later `GET` request subscribes to SSE output.
- The exact `GET` route shape for SSE is not decided yet.
- SSE output is planned to be implemented with Redis Stream.
- `task_id` is used by the later `GET` request to listen for SSE output and will be part of the Redis key.
- This project will not use database foreign key constraints.
- `conversation.status` is a conversation-level running lock; `handle_chat` is expected to add a distributed lock later.
- When a user restarts from a specific turn, `handle_chat` deletes messages and tasks from that turn onward before recreating the turn.
- Task state snapshots are owned by their task; when deleting tasks from a turn onward, `handle_chat` deletes the states returned from those removed tasks.
- `handle_chat` writes the current user message to `messages` with `role = "user"` and creates the current turn's `tasks` row with `state = NULL`.
- `handle_chat` loads the current turn input state before creating the task: turn 1 uses `app.agents.orchestrator.get_init_state()`, later turns load the previous turn's task state from `tasks` and then `states`, then apply `state_from_dict`.
- `handle_chat` handles problem and submission attachments in the transaction before starting the runner and appends attachment `HumanMessage` objects to the current input state.
- Problem attachment messages exclude `Problem.solution`, but the full fetched `Problem` remains in `state["problems"]`.
- Submission attachments fetch the submission and its problem, append both to state, and store the submitted source code as a separate `CodeFileContext`.
- `handle_chat` collects code attachment source text into `code_sources: list[str]` and starts `run_task` asynchronously after the transaction.
- `run_task` receives `code_sources: list[str]` and the current input state; it does not receive or interpret raw chat attachments.
- Repository methods support an optional external database connection via `conn=...`; pass the same connection to multiple repository calls when they must share one transaction.

## Confirmed Database Design

- `conversations` stores chat sessions.
- `conversations.id` is the conversation UUID primary key.
- `conversations.user_id` stores the owning TenJudge user id.
- `conversations.title` stores an optional generated/display title.
- `conversations.updated_at` stores the last conversation update time.
- `conversations.current_turn` stores the current turn index for the conversation.
- `conversations.status` is either `running` or `finished`.
- `messages` stores user and agent messages by turn.
- `messages` uses `(conversation_id, turn_index, role)` as its primary key.
- `messages.role` is restricted to `user` or `agent`, so each turn has at most one user message and one agent message.
- `messages.attachments` is for user messages; agent messages do not use attachments.
- `tasks` stores one async agent task per conversation turn.
- `tasks` uses `(conversation_id, turn_index)` as its primary key.
- `tasks.task_id` identifies the async task for Redis Stream/SSE listening.
- `tasks.state` stores the final output state for the current turn after the async agent finishes.
- `states` stores LangGraph state snapshots as JSONB.
- `app.agents.orchestrator.state_to_dict` and `state_from_dict` serialize/deserialize LangChain messages with `messages_to_dict` and `messages_from_dict`.
- `app.agents.orchestrator.State` contains `messages`, `code_files`, `problems`, `submissions`, `code_file_cnt`, `problem_cnt`, `submission_cnt`, `token`, and `user_id`.
- Agent state file, problem, and submission references use outer context IDs (`CodeFileContext.id`, `ProblemContext.id`, `SubmissionContext.id`) as agent-facing stable identifiers; `Problem.problem_id` and `Submission.submission_id` store TenJudge server IDs.

## Agent Working Rules

- Run Python and project commands through `uv` when applicable, for example `uv run python ...`.
- If you find a problem in the user's code while working, point it out clearly.
- After writing code, if the change reveals or confirms useful project information, record it in this `AGENTS.md`.
- If code and this `AGENTS.md` become inconsistent, update this `AGENTS.md` to match the code.

## Confirmed Response Handling

- `Result.success(data)` returns a unified success response.
- `Result.fail(code, message)` returns a unified failure response.
- `BizException` is the business exception type.
- `Code` defines business response codes.
- `register_exception_handlers(app)` registers global handlers for:
  - `BizException`
  - `Exception`

## Confirmed Dependencies

The project declares these dependencies in `pyproject.toml`:

- `fastapi`
- `langchain`
- `langchain-openai`
- `langchain-postgres`
- `langgraph`
- `pip`
- `psycopg[binary,pool]`
- `pydantic`
- `pydantic-settings`
- `redis`
- `uvicorn`
