# AGENTS.md

## Project

This repository is the agent part of an OJ project.

## Confirmed Runtime

- Project name: `tenjudge-agent`
- Python requirement: `>=3.14`
- Dependency manager files present: `pyproject.toml`, `uv.lock`
- Main FastAPI app entrypoint: `app.main:app`
- Development command recorded in source:

```bash
uv run uvicorn app.main:app --reload
```

## Confirmed Application Structure

- `app/main.py` creates the FastAPI application.
- `app/router/chat.py` defines the `/agent/chat` route.
- `app/service/chat.py` contains chat service code.
- `app/core/response.py` contains unified response and exception handling code.
- `app/core/config.py` contains configuration code.
- `app/core/db.py` contains database lifecycle code.

## Agent Working Rules

- Run Python and project commands through `uv` when applicable, for example `uv run python ...`.
- If you find a problem in the user's code while working, point it out clearly.

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
