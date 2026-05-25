import json
import logging
import re
from typing import Any

from langchain_core.tools import tool
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.core.config import settings


logger = logging.getLogger(__name__)

AGENT_DB_TOOL_MAX_ROWS = 100
AGENT_DB_TOOL_STATEMENT_TIMEOUT_MS = 3000
AGENT_DB_TOOL_MAX_FIELD_CHARS = 4000
AGENT_DB_TOOL_MAX_RESULT_CHARS = 60000

ALLOWED_VIEWS = {
    "problem",
    "problem_tag",
    "users",
    "contest",
    "contest_problem",
    "contest_participant",
}

FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b("
    r"alter|analyze|begin|call|commit|copy|create|delete|do|drop|execute|"
    r"explain|grant|insert|listen|lock|merge|notify|refresh|reset|revoke|"
    r"rollback|set|truncate|update|vacuum"
    r")\b",
    re.IGNORECASE,
)
RELATION_PATTERN = re.compile(
    r"\b(?:from|join)\s+([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)",
    re.IGNORECASE,
)
CTE_PATTERN = re.compile(
    r"(?:\bwith\b|,)\s+(?:recursive\s+)?([a-z_][a-z0-9_]*)"
    r"(?:\s*\([^)]*\))?\s+as\s*\(",
    re.IGNORECASE,
)
DANGEROUS_FUNCTION_PATTERN = re.compile(
    r"\b("
    r"current_setting|cursor_to_xml|database_to_xml|"
    r"dblink|lo_export|lo_import|pg_ls_dir|pg_read_binary_file|"
    r"pg_read_file|pg_sleep|pg_stat_file|query_to_xml|schema_to_xml|"
    r"set_config"
    r")\s*\(",
    re.IGNORECASE,
)
STRING_LITERAL_PATTERN = re.compile(r"'(?:''|[^'])*'")


class QueryOjDatabaseInput(BaseModel):
    sql: str = Field(
        min_length=1,
        description="One read-only SELECT query over the allowed OJ views.",
    )


class UnsafeSqlError(ValueError):
    pass


def _strip_single_trailing_semicolon(sql: str) -> str:
    stripped = sql.strip()
    if not stripped:
        raise UnsafeSqlError("SQL is empty.")

    masked_sql = STRING_LITERAL_PATTERN.sub("''", stripped)
    semicolon_count = masked_sql.count(";")
    if semicolon_count > 1 or (semicolon_count == 1 and not masked_sql.endswith(";")):
        raise UnsafeSqlError("Only one SQL statement is allowed.")

    return stripped[:-1].strip() if stripped.endswith(";") else stripped


def _extract_cte_names(sql: str) -> set[str]:
    return {match.group(1).lower() for match in CTE_PATTERN.finditer(sql)}


def _validate_relation_names(sql: str) -> None:
    cte_names = _extract_cte_names(sql)
    for match in RELATION_PATTERN.finditer(sql):
        relation = match.group(1).lower()
        if relation in cte_names:
            continue

        if "." in relation:
            schema_name, view_name = relation.split(".", 1)
            if schema_name != "agent_read" or view_name not in ALLOWED_VIEWS:
                raise UnsafeSqlError(f"Relation is not available: {relation}.")
            continue

        if relation not in ALLOWED_VIEWS:
            raise UnsafeSqlError(f"Relation is not available: {relation}.")


def validate_read_only_sql(sql: str) -> str:
    cleaned_sql = _strip_single_trailing_semicolon(sql)
    lowered = cleaned_sql.lstrip().lower()
    masked_sql = STRING_LITERAL_PATTERN.sub("''", cleaned_sql)

    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise UnsafeSqlError("Only SELECT or WITH SELECT queries are allowed.")

    # 1. 注释和引号标识符会让轻量校验变复杂；公开 view 都使用简单小写名称。
    if "--" in masked_sql or "/*" in masked_sql or "*/" in masked_sql:
        raise UnsafeSqlError("SQL comments are not allowed.")
    if '"' in masked_sql or "`" in masked_sql:
        raise UnsafeSqlError("Quoted identifiers are not allowed.")

    if FORBIDDEN_SQL_PATTERN.search(masked_sql):
        raise UnsafeSqlError("Only read-only SELECT queries are allowed.")
    if DANGEROUS_FUNCTION_PATTERN.search(masked_sql):
        raise UnsafeSqlError("This SQL function is not allowed.")

    _validate_relation_names(masked_sql)
    return cleaned_sql


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


async def _execute_query(sql: str) -> tuple[list[str], list[dict[str, Any]], bool]:
    if not settings.AGENT_DB_TOOL_DATABASE_URL:
        raise RuntimeError("AGENT_DB_TOOL_DATABASE_URL is empty")

    max_rows = _bounded_int(AGENT_DB_TOOL_MAX_ROWS, minimum=1, maximum=1000)
    fetch_limit = max_rows + 1
    timeout_ms = _bounded_int(
        AGENT_DB_TOOL_STATEMENT_TIMEOUT_MS,
        minimum=100,
        maximum=30000,
    )
    wrapped_sql = f"SELECT * FROM ({sql}) AS agent_tool_query LIMIT %s"

    async with await AsyncConnection.connect(
        settings.AGENT_DB_TOOL_DATABASE_URL,
        row_factory=dict_row,
    ) as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                # 1. 数据库账号本身应是只读账号；这里再用事务级约束做第二层保险。
                await cur.execute("SET TRANSACTION READ ONLY")
                await cur.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{timeout_ms}ms",),
                )
                await cur.execute(
                    "SELECT set_config('search_path', %s, true)",
                    ("agent_read",),
                )
                await cur.execute(wrapped_sql, (fetch_limit,))
                fetched_rows = await cur.fetchall()
                columns = [column.name for column in cur.description]

    truncated = len(fetched_rows) > max_rows
    return columns, fetched_rows[:max_rows], truncated


TOOL_DESCRIPTION = """Run one read-only SELECT on OJ public views. Views:
problem(id, author_id, visibility, checker, time_limit, memory_limit, name, statement, solution, difficulty, version, test_case_num) public problems only;
problem_tag(problem_id, tag) public problem tags;
users(id, username, created_at, role, rating, max_rating, bio, solved_count) no password/email;
contest(id, name, start_time, end_time, freeze_time, board_refreshed_at, penalty_per_wrong);
contest_problem(contest_id, problem_id, problem_index, problem_name, problem_visibility) includes private contest problem name/index only;
contest_participant(contest_id, user_id, username, solved_count, penalty, last_accepted_time, problem_results).
Submissions are not available. Return value is JSON with columns, rows, row_count, truncated."""


@tool(
    args_schema=QueryOjDatabaseInput,
    description=TOOL_DESCRIPTION,
)
async def query_oj_database(sql: str) -> str:
    """Run a safe read-only SQL query against the OJ view schema."""
    cleaned_sql = validate_read_only_sql(sql)
    logger.info("执行 OJ 数据库查询 sql=%s", cleaned_sql[:2000])
    columns, rows, truncated = await _execute_query(cleaned_sql)
    return _format_result(cleaned_sql, columns, rows, truncated)
