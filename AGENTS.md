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
- `app/router/chat.py` defines the `/agent/chat` route, owns the chat request/response DTO models, and exposes `/agent/chat/{task_id}/events` for SSE subscription.
- `app/service/chat.py` contains chat service code.
- `app/service/tenjudge_server.py` contains outbound HTTP calls to the TenJudge server, including current-user, problem, and submission lookup.
- `app/agents/context.py` contains agent context models such as `CodeFile`, `CodeFileContext`, `Problem`, `ProblemContext`, `Submission`, `SubmissionDetail`, and `SubmissionContext`.
- `app/agents/code_summarize_agent.py` contains `summarize_code_files`, which uses structured LLM output to turn raw code attachments plus conversation context into `CodeFile` objects.
- `app/agents/title_agent.py` contains `summarize_title`, which uses structured LLM output to create a short display title from the first user message.
- `app/agents/plan_agent.py` contains `make_plan`, which uses `LLM("medium")` structured output to create English execution plans from conversation messages, optional planning guidance, and LangChain tool metadata.
- `app/agents/orchestrator.py` owns the chat agent `State` TypedDict definition, state serialization helpers, agent tool entrypoint, LangGraph nodes, and the compiled `agent` graph.
- `app/repository/messages.py` contains message persistence code; messages use `(conversation_id, turn_index, role)` as the primary key and expose `get_by_key`, `delete_by_key`, and `delete_from_turn`.
- `app/repository/tasks.py` contains task persistence code; tasks use `(conversation_id, turn_index)` as the primary key and expose `get_by_key`, `get_by_task_id`, `delete_by_key`, and `delete_from_turn`.
- `app/repository/states.py` contains LangGraph state persistence code and exposes `delete_by_ids` for deleting task-owned state snapshots.
- `app/core/response.py` contains unified response and exception handling code.
- `app/core/config.py` contains configuration code.
- `app/core/db.py` contains database lifecycle code.
- `app/core/redis.py` contains the global async Redis client and Redis shutdown helper.

## Confirmed Architecture Plan

- Chat submission is implemented as two phases: a `POST /agent/chat` request submits chat input, and `GET /agent/chat/{task_id}/events` subscribes to SSE output.
- SSE output is implemented from Redis Stream reads.
- `task_id` is used by the later `GET` request to listen for SSE output and is part of the Redis key `agent:task:{task_id}:events`.
- Redis Stream event fields are `event` and `data`; supported event values are `progress`, `message`, `title`, `failed`, and `done`, and `data` is a plain string.
- `handle_chat` creates the task Redis Stream before returning from `POST /agent/chat` by writing `progress` with data `Preparing task`.
- Redis Stream keys use `app.core.config.Settings.REDIS_STREAM_TTL_SECONDS` as their TTL; `GET /agent/chat/{task_id}/events` returns `NOT_FOUND` when the stream key no longer exists.
- SSE subscription validates ownership by looking up the task by `task_id`, loading its conversation, and comparing `conversation.user_id` with the authenticated TenJudge user id.
- SSE subscription supports `Last-Event-ID` using Redis Stream entry ids and sends `: ping` heartbeat comments on Redis read timeouts.
- This project will not use database foreign key constraints.
- `conversation.status` is a conversation-level running lock; `handle_chat` is expected to add a distributed lock later.
- When a user restarts from a specific turn, `handle_chat` deletes messages and tasks from that turn onward before recreating the turn.
- Task state snapshots are owned by their task; when deleting tasks from a turn onward, `handle_chat` deletes the states returned from those removed tasks.
- `handle_chat` writes the current user message to `messages` with `role = "user"` and creates the current turn's `tasks` row with `state = NULL`.
- `handle_chat` loads the current turn input state before creating the task: turn 1 uses `app.agents.orchestrator.get_init_state()`, later turns load the previous turn's task state from `tasks` and then `states`, then apply `state_from_dict`.
- `handle_chat` handles problem and submission attachments in the transaction before starting the runner and appends attachment `HumanMessage` objects to the current input state.
- Problem attachment messages exclude `Problem.solution`, but the full fetched `Problem` remains in `state["problems"]`.
- Submission attachments fetch the submission and its problem, append both to state, and store the submitted source code as a separate `CodeFileContext`.
- TenJudge server HTTP failures, invalid JSON, and response validation failures are allowed to propagate to the global exception handler; only TenJudge business response codes are converted to `BizException`.
- `handle_chat` collects code attachment source text into `code_sources: list[str]` and starts `run_task` asynchronously after the transaction.
- `run_task` receives `conversation_id`, `turn_index`, `task_id`, `code_sources: list[str]`, and the current input state; it does not receive or interpret raw chat attachments.
- On turn 1, `run_task` starts a non-blocking background title task that calls `summarize_title(message)`, updates `conversations.title`, and emits Redis `title` with the generated title as plain-string `data`.
- Title updates are guarded by `(conversation_id, turn_index, task_id)` so a stale first-turn background task cannot overwrite the title after the user restarts from turn 1.
- Title generation failures are logged and do not affect the main agent task or `done` event timing; because SSE closes on `done`, a slow title event may be written after the current SSE connection has ended.
- Code attachment summarization uses `LLM("low")`, receives complete historical messages plus the current user message, returns English descriptions, and preserves the input code order.
- `run_task` appends summarized code attachments to both `state["code_files"]` and `state["messages"]` before appending the current user message.
- `run_task` calls `make_plan` with `app.agents.orchestrator.AGENT_TOOLS` after appending the current user message, then appends the formatted internal plan as a `SystemMessage` to long-term `state["messages"]`.
- `run_task` executes `app.agents.orchestrator.agent.astream` with stream modes `messages`, `custom`, and `values`.
- `run_task` forwards custom stream chunks as Redis `progress` events and `agent_node` message chunks as Redis `message` events.
- `run_task` uses the latest `values` stream state as the final state for persistence.
- On success, `run_task` persists the final state, updates `tasks.state`, inserts the `agent` message, marks the conversation `finished`, and emits Redis `done`.
- On failure, `run_task` persists a failed state with an `AIMessage`, inserts a failed `agent` message, marks the conversation `finished`, and emits Redis `failed` then `done`.
- Planning uses LangChain `BaseTool` objects directly; tool names, descriptions, input schemas, and return type schemas are extracted for `plan_agent`, while `@tool(parse_docstring=True)` is recommended but not required.
- The LangGraph orchestration uses a simple ReAct loop: `START -> agent_node -> tools_node -> agent_node`, and routes directly to `END` when the agent returns no tool calls.
- `app.agents.orchestrator.AGENT_TOOLS` is the shared future entrypoint for business tools and is currently an empty list until real tools are implemented.
- `finish_node` has been removed; user-facing output is streamed from `agent_node` message chunks.
- `get_init_state()` initializes `state["messages"]` with `SystemMessage("You are the TenJudge online judge platform assistant.")`.
- ReAct rounds are capped by `app.core.config.Settings.AGENT_MAX_REACT_ROUNDS`, counted as `AIMessage` results since the latest `HumanMessage`; the near-limit warning threshold is `AGENT_REACT_ROUND_WARNING_REMAINING`.
- Repository methods support an optional external database connection via `conn=...`; pass the same connection to multiple repository calls when they must share one transaction.

## Confirmed Database Design

- `conversations` stores chat sessions.
- `conversations.id` is the conversation UUID primary key.
- Newly generated conversation ids use UUIDv7.
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
- Newly generated task ids use UUIDv7.
- `tasks.task_id` should be unique because SSE subscription only receives `task_id` and uses it to locate exactly one task.
- `TaskRepository.get_by_task_id` loads a task from its async `task_id` for SSE subscription authorization.
- `TaskRepository.update_state_by_task_id` writes the persisted final state id for an async task.
- `tasks.state` stores the final output state for the current turn after the async agent finishes.
- `states` stores LangGraph state snapshots as JSONB.
- `app.agents.orchestrator.state_to_dict` and `state_from_dict` serialize/deserialize LangChain messages with `messages_to_dict` and `messages_from_dict`.
- `app.agents.orchestrator.State` contains `messages`, `code_files`, `problems`, `submissions`, `code_file_cnt`, `problem_cnt`, `submission_cnt`, `token`, and `user_id`.
- Agent state file, problem, and submission references use outer context IDs (`CodeFileContext.id`, `ProblemContext.id`, `SubmissionContext.id`) as agent-facing stable identifiers; `Problem.problem_id` and `Submission.submission_id` store TenJudge server IDs.

## Agent Working Rules

- Run Python and project commands through `uv` when applicable, for example `uv run python ...`.
- If you find a problem in the user's code while working, point it out clearly.
- When writing longer or structurally complex code, prefer Chinese numbered comments like `# 1. ...`, `# 2. ...`; use sub-numbering like `# 2.1 ...` and `# 2.2 ...` when a step has smaller parts. Add short Chinese end-of-line comments at key points when they clarify important state changes or side effects.
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
