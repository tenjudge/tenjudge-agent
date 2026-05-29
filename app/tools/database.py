import json
import logging
from typing import Any

from langchain_core.tools import tool
from psycopg import AsyncConnection, Error as PsycopgError
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.core.config import settings


logger = logging.getLogger(__name__)

AGENT_DB_TOOL_MAX_ROWS = 100
AGENT_DB_TOOL_MAX_FIELD_CHARS = 4000
AGENT_DB_TOOL_MAX_RESULT_CHARS = 60000


class QueryOjDatabaseInput(BaseModel):
    sql: str = Field(
        min_length=1,
        description="One SELECT query over the allowed OJ views.",
    )


def _normalize_sql(sql: str) -> str:
    stripped = sql.strip()
    if not stripped:
        raise ValueError("SQL is empty.")

    return stripped[:-1].strip() if stripped.endswith(";") else stripped


def _bounded_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _safe_json_value(value: Any) -> Any:
    field_limit = _bounded_int(
        AGENT_DB_TOOL_MAX_FIELD_CHARS,
        minimum=200,
        maximum=20000,
    )
    payload = json.dumps(value, ensure_ascii=False, default=str)
    if len(payload) <= field_limit:
        return json.loads(payload)
    return payload[:field_limit] + "...[truncated]"


def _format_result(
        sql: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        truncated: bool,
) -> str:
    safe_rows = [
        {key: _safe_json_value(value) for key, value in row.items()}
        for row in rows
    ]
    result = {
        "success": True,
        "columns": columns,
        "rows": safe_rows,
        "row_count": len(safe_rows),
        "truncated": truncated,
        "sql": sql,
    }

    max_result_chars = _bounded_int(
        AGENT_DB_TOOL_MAX_RESULT_CHARS,
        minimum=2000,
        maximum=200000,
    )
    payload = json.dumps(result, ensure_ascii=False, default=str)
    while len(payload) > max_result_chars and safe_rows:
        safe_rows.pop()
        result["row_count"] = len(safe_rows)
        result["truncated"] = True
        result["note"] = "Result was shortened to fit the tool output size limit."
        payload = json.dumps(result, ensure_ascii=False, default=str)

    return payload


def _format_error_result(sql: str, error: Exception) -> str:
    # 1. 工具错误返回给模型处理，避免一次 SQL 写错中断整轮任务。
    error_payload: dict[str, Any] = {
        "type": type(error).__name__,
        "message": str(error),
    }

    if isinstance(error, PsycopgError):
        # 2. PostgreSQL 的主错误信息更短，sqlstate 便于模型和日志定位错误类别。
        message = getattr(error.diag, "message_primary", None)
        if message:
            error_payload["message"] = message
        if error.sqlstate:
            error_payload["sqlstate"] = error.sqlstate

    return json.dumps(
        {
            "success": False,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "sql": sql,
            "error": error_payload,
        },
        ensure_ascii=False,
        default=str,
    )


async def _execute_query(sql: str) -> tuple[list[str], list[dict[str, Any]], bool]:
    if not settings.AGENT_DB_TOOL_DATABASE_URL:
        raise RuntimeError("AGENT_DB_TOOL_DATABASE_URL is empty")

    max_rows = _bounded_int(AGENT_DB_TOOL_MAX_ROWS, minimum=1, maximum=1000)
    fetch_limit = max_rows + 1

    # 1. 不对外层查询使用 psycopg 占位符；内层 SQL 可能包含 ILIKE '%dp%'，
    #    参数化执行会把字符串字面量里的 %d/%s 误解析成占位符。
    wrapped_sql = f"SELECT * FROM ({sql}) AS agent_tool_query LIMIT {fetch_limit}"

    async with await AsyncConnection.connect(
        settings.AGENT_DB_TOOL_DATABASE_URL,
        row_factory=dict_row,
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(wrapped_sql)
            fetched_rows = await cur.fetchall()
            columns = [column.name for column in cur.description]

    truncated = len(fetched_rows) > max_rows
    return columns, fetched_rows[:max_rows], truncated


TOOL_DESCRIPTION = """Run one read-only SELECT against TenJudge OJ read views.

Available views:
- problem(id, author_id, visibility, checker, time_limit, memory_limit, name, statement, solution, difficulty, version, test_case_num): only public problems are visible here.
- problem_tag(problem_id, tag): tag rows attached to visible problems.
- users(id, username, created_at, role, rating, max_rating, bio, solved_count): public user fields; password and email are hidden.
- contest(id, name, start_time, end_time, freeze_time, board_refreshed_at, penalty_per_wrong).
- contest_problem(contest_id, problem_id, problem_index, problem_name, problem_visibility): contest-to-problem mapping.
- contest_participant(contest_id, user_id, username, solved_count, penalty, last_accepted_time, problem_results): contest scoreboard rows.

Known field values:
- visibility fields use public/private; the problem view is already filtered to public.
- users.role uses user, admin, super_admin.
- problem.difficulty is a Codeforces-style integer rating, for example 800, 1600, or 2100.
- problem_tag.tag values: 2-sat, binary search, bitmasks, brute force, chinese remainder theorem, combinatorics, communication, constructive algorithms, data structures, dfs and similar, divide and conquer, dp, dsu, expression parsing, fft, flows, games, geometry, graph matchings, graphs, greedy, hashing, implementation, interactive, math, matrices, meet-in-the-middle, number theory, probabilities, schedules, shortest paths, sortings, string suffix structures, strings, ternary search, trees, two pointers.

Submission tables are unavailable. Return value is JSON with success, columns, rows, row_count, truncated, sql, and error when success=false."""


@tool(
    args_schema=QueryOjDatabaseInput,
    description=TOOL_DESCRIPTION,
)
async def query_oj_database(sql: str) -> str:
    """Run a SQL query against the restricted OJ view schema."""
    cleaned_sql = sql.strip()
    try:
        cleaned_sql = _normalize_sql(sql)
        logger.info("执行 OJ 数据库查询 sql=%s", cleaned_sql[:2000])
        columns, rows, truncated = await _execute_query(cleaned_sql)
        return _format_result(cleaned_sql, columns, rows, truncated)
    except (ValueError, PsycopgError) as error:
        logger.warning("OJ 数据库查询失败 sql=%s error=%s", cleaned_sql[:2000], error)
        return _format_error_result(cleaned_sql, error)
