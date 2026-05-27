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
- `app/router/chat.py` defines the `/chat` route, owns the chat request/response DTO models, and exposes `/chat/{task_id}/events` for SSE subscription.
- `app/router/conversation.py` defines conversation routes, including `/conversations` for listing the authenticated user's conversations and `/conversations/{conversation_id}` for loading one conversation's detail and messages.
- `app/service/chat.py` contains chat service code.
- `app/service/tenjudge_server.py` contains outbound HTTP calls to the TenJudge server, including current-user, problem, submission lookup, judge submission, and judge-result polling.
- `app/agents/context.py` contains agent context models such as `CodeFile`, `CodeFileContext`, `Problem`, `ProblemContext`, `Submission`, `SubmissionDetail`, and `SubmissionContext`, plus shared code file helpers including LF newline normalization and state lookup helpers for `CodeFileContext`.
- `app/agents/code_summarize_agent.py` contains `summarize_code_files`, which uses structured LLM output to turn raw code attachments plus conversation context into `CodeFile` objects.
- `app/agents/title_agent.py` contains `summarize_title`, which uses structured LLM output to create a short display title from the first user message.
- `app/agents/plan_agent.py` contains `make_plan`, which uses `LLM("medium")` structured output to create English execution plans from conversation messages, optional planning guidance, and LangChain tool metadata.
- `app/agents/orchestrator.py` owns the chat agent `State` TypedDict definition, state serialization helpers, agent tool entrypoint, LangGraph nodes, and the compiled `agent` graph.
- `app/tools/database.py` contains the `query_oj_database` LangChain tool for read-only SQL queries over restricted OJ views.
- `app/tools/judge.py` contains the `submit_code_for_judge` LangChain tool for submitting an existing state code file to TenJudge judge and briefly polling for the result.
- `app/tools/code_file.py` contains LangChain tools for creating, replacing, overwriting, metadata-updating, and save-as editing state code files.
- `app/tools/misc.py` contains small LangChain tools such as `get_current_time` and `get_current_user_id`.
- `app/repository/messages.py` contains message persistence code; messages use `(conversation_id, turn_index, role)` as the primary key and expose `get_by_key`, `delete_by_key`, and `delete_from_turn`.
- `app/repository/tasks.py` contains task persistence code; tasks use `(conversation_id, turn_index)` as the primary key and expose `get_by_key`, `get_by_task_id`, `delete_by_key`, and `delete_from_turn`.
- `app/repository/states.py` contains LangGraph state persistence code and exposes `delete_by_ids` for deleting task-owned state snapshots.
- `app/repository/agent_schema.sql` contains this project's `conversations`, `messages`, `tasks`, and `states` table DDL without database foreign keys.
- `app/repository/agent_tool_views.sql` contains the `agent_read` schema views and restricted `tenjudge_agent_tool` role grants for database tool access.
- `app/core/response.py` contains unified response and exception handling code.
- `app/core/config.py` contains configuration code.
- `app/core/db.py` contains database lifecycle code.
- `app/core/redis.py` contains the global async Redis client and Redis shutdown helper.
- `app/main.py` configures FastAPI CORS for local frontend origins `http://localhost:5173` and `http://127.0.0.1:5173`, with credentials and custom headers allowed.

## Confirmed Architecture Plan

- Chat submission is implemented as two phases: a `POST /chat` request submits chat input, and `GET /chat/{task_id}/events` subscribes to SSE output.
- Conversation list retrieval is implemented as `GET /conversations`, returns only `id`, `title`, and `next_cursor`, and sorts conversations by `updated_at DESC`.
- `GET /conversations` uses cursor pagination with `limit` and opaque `cursor`; the first request omits `cursor`, and `next_cursor = null` means there are no more conversations.
- Conversation detail retrieval is implemented as `GET /conversations/{conversation_id}`, returns `id`, `title`, `status`, `running_task_id`, and full historical `messages`.
- `GET /conversations/{conversation_id}` returns user message attachments exactly as stored in `messages.attachments`; when `conversation.status = running`, `running_task_id` is loaded from the current turn task so the frontend can subscribe to the task SSE stream.
- SSE output is implemented from Redis Stream reads.
- `task_id` is used by the later `GET` request to listen for SSE output and is part of the Redis key `agent:task:{task_id}:events`.
- Redis Stream event fields are `event` and `data`; supported event values are `progress`, `message`, `title`, `failed`, and `done`, and `data` is a plain string.
- `handle_chat` creates the task Redis Stream before returning from `POST /chat` by writing `progress` with data `Planning`.
- Redis Stream keys use `app.core.config.Settings.REDIS_STREAM_TTL_SECONDS` as their TTL; `GET /chat/{task_id}/events` returns `NOT_FOUND` when the stream key no longer exists.
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
- TenJudge judge submission uses `POST /submit/judge` with `isAgent = true`, sends only `problemId`, `language`, `code`, and `isAgent`, and does not send `contestId`.
- `submit_judge_and_wait` polls `GET /submit/{submission_id}` every 1 second by default, treats only `PENDING` as unfinished, and returns `JudgeWaitResult(success=False, submission=latest_submission, message=...)` if the submission is still pending after 20 seconds.
- If judge submission succeeds but submission lookup returns a TenJudge business failure, `submit_judge_and_wait` returns `JudgeWaitResult(success=False, submission=None, message=...)` instead of raising that lookup `BizException`.
- `handle_chat` collects code attachment source text into `code_sources: list[str]`, normalizes code attachment and submission-code line endings to LF, and starts `run_task` asynchronously after the transaction.
- `run_task` receives `conversation_id`, `turn_index`, `task_id`, `code_sources: list[str]`, and the current input state; it does not receive or interpret raw chat attachments.
- `run_task` normalizes `code_sources` line endings to LF before summarizing and appending them to `state["code_files"]`.
- On turn 1, `run_task` starts a non-blocking background title task that calls `summarize_title(message)`, updates `conversations.title`, and emits Redis `title` with the generated title as plain-string `data`.
- Title updates are guarded by `(conversation_id, turn_index, task_id)` so a stale first-turn background task cannot overwrite the title after the user restarts from turn 1.
- Title generation failures are logged and do not affect the main agent task or `done` event timing; because SSE closes on `done`, a slow title event may be written after the current SSE connection has ended.
- Code attachment summarization uses `LLM("low")`, receives complete historical messages plus the current user message, returns English descriptions, and preserves the input code order.
- `run_task` appends summarized code attachments to both `state["code_files"]` and `state["messages"]` before appending the current user message.
- `run_task` calls `make_plan` with `app.agents.orchestrator.AGENT_TOOLS` after appending the current user message, then appends the formatted internal plan as a `SystemMessage` to long-term `state["messages"]`.
- Before `run_task` enters the compiled LangGraph agent, it emits Redis `progress` with English data `Thinking`.
- `run_task` executes `app.agents.orchestrator.agent.astream` with stream modes `messages`, `custom`, and `values`.
- `run_task` forwards custom stream chunks as Redis `progress` events and `agent_node` message chunks as Redis `message` events.
- `run_task` uses the latest `values` stream state as the final state for persistence.
- On success, `run_task` persists the final state, updates `tasks.state`, inserts the `agent` message, marks the conversation `finished`, and emits Redis `done`.
- On failure, `run_task` persists a failed state with an `AIMessage`, inserts a failed `agent` message, marks the conversation `finished`, and emits Redis `failed` then `done`.
- Planning uses LangChain `BaseTool` objects directly; tool names, descriptions, input schemas, and return type schemas are extracted for `plan_agent`, while `@tool(parse_docstring=True)` is recommended but not required.
- Successful planning is logged with a Chinese marker line `【计划完成】`, followed by the full formatted internal plan on later lines.
- The LangGraph orchestration uses a simple ReAct loop: `START -> agent_node -> tools_node -> agent_node`, and routes directly to `END` when the agent returns no tool calls.
- `agent_node` does not log model-call start metadata; after the model returns, it logs non-empty model content with `【模型输出】` followed by the full content on later lines, and logs each requested tool call separately as `【工具调用】<tool_name>`.
- The LangGraph `tools_node` wraps tool calls and emits English custom progress chunks before tool execution, such as `Querying database`, `Submitting code for judging`, `Creating code file`, `Updating code file`, `Overwriting code file`, `Updating code file metadata`, `Checking current time`, and `Checking current user`.
- `app.agents.orchestrator.AGENT_TOOLS` is the shared entrypoint for business tools and currently includes `query_oj_database`, `submit_code_for_judge`, `create_code_file`, `replace_code_file_content`, `replace_code_file_content_as_new`, `overwrite_code_file`, `update_code_file_metadata`, `get_current_time`, and `get_current_user_id`.
- `query_oj_database` accepts one read-only `SELECT` / `WITH SELECT` query and returns JSON containing `columns`, `rows`, `row_count`, `truncated`, and `sql`.
- `query_oj_database` should connect using `Settings.AGENT_DB_TOOL_DATABASE_URL`, which must point at the restricted `tenjudge_agent_tool` database role.
- Database tool result limits are module constants in `app/tools/database.py`: `AGENT_DB_TOOL_MAX_ROWS`, `AGENT_DB_TOOL_STATEMENT_TIMEOUT_MS`, `AGENT_DB_TOOL_MAX_FIELD_CHARS`, and `AGENT_DB_TOOL_MAX_RESULT_CHARS`.
- Database tool SQL access is limited to `agent_read` views: `problem`, `problem_tag`, `users`, `contest`, `contest_problem`, and `contest_participant`; submission tables are intentionally unavailable.
- `submit_code_for_judge` accepts `code_file_id` and `problem_id` only; it reads source code, language, and token from injected LangGraph state.
- `submit_code_for_judge` does not write the new submission back to `state["submissions"]`; it returns a JSON string with `success`, `message`, `code_file_id`, `problem_id`, `language`, and `submission`, with `submission.code` omitted.
- `submit_code_for_judge.success` means the tool obtained a final judge result, not that the submitted code was accepted; final verdicts such as `WRONG_ANSWER` still return `success = true`.
- Code file write tools return `langgraph.types.Command` updates with a `ToolMessage` JSON payload and update `state["code_files"]`/`state["code_file_cnt"]` when successful.
- Code file tool success payloads return the complete current file content plus `code_file_id`, `description`, and `language`; failure payloads use `success=false` and a natural-language `message` without mutating code files.
- Code file tool descriptions mark returned identifiers and metadata as private execution context for later tool calls only; success `message` values intentionally avoid internal identifiers so the user-facing model is less likely to expose them.
- `create_code_file` requires non-empty `description`, `language`, and non-empty `content`, creates the next `code_file_N`, and normalizes content line endings to LF.
- `replace_code_file_content` edits an existing `code_file_id` by exact `old_string`/`new_string` replacement, supports `replace_all=false` by default, fails without mutation on missing or duplicate matches unless `replace_all=true`, and allows empty `old_string` only when the target file content is empty.
- `replace_code_file_content_as_new` applies the same exact replacement rules to a source code file but appends the result as a new `code_file_N`; `description` and `language` are required for the new file.
- `overwrite_code_file` replaces an existing file with non-empty complete `content` and optional metadata updates.
- `update_code_file_metadata` changes only `description` and/or `language`, requires at least one of them, and keeps source content unchanged except LF newline normalization.
- `get_current_time` returns the current time in `Asia/Shanghai`; `get_current_user_id` reads the authenticated user id from injected LangGraph state.
- `finish_node` has been removed; user-facing output is streamed from `agent_node` message chunks.
- `get_init_state()` initializes `state["messages"]` with `INITIAL_SYSTEM_PROMPT`, which identifies the assistant as the TenJudge platform assistant and tells the user-facing agent to answer in the user's latest-message language, separate private execution context from user-facing answers, treat the internal code file system as an execution aid unknown to users, avoid hidden workflow narration and unnecessary permission prompts, avoid menus of next steps, and not copy internal English planning/tool wording into final answers.
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
