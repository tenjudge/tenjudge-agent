import json
from types import SimpleNamespace

from psycopg.errors import UndefinedColumn
import pytest

from app.tools import database


class FakeCursor:
    def __init__(self):
        self.calls = []
        self.description = [SimpleNamespace(name="id")]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))

    async def fetchall(self):
        return [{"id": 1}]


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def cursor(self):
        return self.cursor_obj


def test_normalize_sql_only_strips_whitespace_and_trailing_semicolon():
    sql = """
    WITH recent AS (
        SELECT "id", name, 'semi;colon' AS marker
        FROM agent_read.problem
        WHERE name ILIKE '%dp%' -- comment ; is not a statement separator
    )
    SELECT * FROM recent WHERE marker = $$semi;colon$$;
    """

    cleaned_sql = database._normalize_sql(sql)

    assert cleaned_sql.startswith("WITH recent")
    assert cleaned_sql.endswith("$$semi;colon$$")


@pytest.mark.asyncio
async def test_execute_query_keeps_like_percent_patterns_out_of_psycopg_params(monkeypatch):
    conn = FakeConnection()

    async def fake_connect(*args, **kwargs):
        return conn

    monkeypatch.setattr(
        database,
        "settings",
        SimpleNamespace(AGENT_DB_TOOL_DATABASE_URL="postgresql://example/test"),
    )
    monkeypatch.setattr(database.AsyncConnection, "connect", fake_connect)

    columns, rows, truncated = await database._execute_query(
        "SELECT id FROM problem WHERE name ILIKE '%dp%'",
    )

    final_sql, final_params = conn.cursor_obj.calls[-1]
    assert columns == ["id"]
    assert rows == [{"id": 1}]
    assert truncated is False
    assert len(conn.cursor_obj.calls) == 1
    assert "ILIKE '%dp%'" in final_sql
    assert "LIMIT %s" not in final_sql
    assert final_sql.endswith("LIMIT 101")
    assert final_params is None


@pytest.mark.asyncio
async def test_query_oj_database_success_result_has_success_flag(monkeypatch):
    async def fake_execute_query(sql):
        return ["id"], [{"id": 1}], False

    monkeypatch.setattr(database, "_execute_query", fake_execute_query)

    result = await database.query_oj_database.ainvoke({"sql": " SELECT id FROM problem; "})
    payload = json.loads(result)

    assert payload["success"] is True
    assert payload["columns"] == ["id"]
    assert payload["rows"] == [{"id": 1}]
    assert payload["sql"] == "SELECT id FROM problem"


@pytest.mark.asyncio
async def test_query_oj_database_returns_error_result_for_database_errors(monkeypatch):
    async def fake_execute_query(sql):
        raise UndefinedColumn("column t.solved_count does not exist")

    monkeypatch.setattr(database, "_execute_query", fake_execute_query)

    result = await database.query_oj_database.ainvoke({"sql": "SELECT bad_column FROM problem"})
    payload = json.loads(result)

    assert payload["success"] is False
    assert payload["columns"] == []
    assert payload["rows"] == []
    assert payload["row_count"] == 0
    assert payload["sql"] == "SELECT bad_column FROM problem"
    assert payload["error"]["type"] == "UndefinedColumn"
    assert payload["error"]["sqlstate"] == "42703"
    assert "column t.solved_count does not exist" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_query_oj_database_returns_error_result_for_empty_sql():
    result = await database.query_oj_database.ainvoke({"sql": "   "})
    payload = json.loads(result)

    assert payload["success"] is False
    assert payload["sql"] == ""
    assert payload["error"] == {
        "type": "ValueError",
        "message": "SQL is empty.",
    }


@pytest.mark.asyncio
async def test_query_oj_database_keeps_configuration_errors_fatal(monkeypatch):
    async def fake_execute_query(sql):
        raise RuntimeError("AGENT_DB_TOOL_DATABASE_URL is empty")

    monkeypatch.setattr(database, "_execute_query", fake_execute_query)

    with pytest.raises(RuntimeError, match="AGENT_DB_TOOL_DATABASE_URL is empty"):
        await database.query_oj_database.ainvoke({"sql": "SELECT id FROM problem"})
