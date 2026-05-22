from app.core.response import BizException, Code, Result, get_http_exception_code
from starlette.exceptions import HTTPException as StarletteHTTPException


def test_result_success_with_data():
    result = Result.success({"id": 1})

    assert result.code == Code.SUCCESS.biz_code
    assert result.message == Code.SUCCESS.message
    assert result.data == {"id": 1}


def test_result_fail_uses_default_message():
    result = Result.fail(Code.UNAUTHORIZED)

    assert result.code == Code.UNAUTHORIZED.biz_code
    assert result.message == Code.UNAUTHORIZED.message
    assert result.data is None


def test_result_fail_uses_custom_message():
    result = Result.fail(Code.PARAM_ERROR, "bad input")

    assert result.code == Code.PARAM_ERROR.biz_code
    assert result.message == "bad input"
    assert result.data is None


def test_biz_exception_uses_code_defaults():
    exc = BizException(Code.FORBIDDEN)

    assert exc.code is Code.FORBIDDEN
    assert exc.biz_code == Code.FORBIDDEN.biz_code
    assert exc.http_status == Code.FORBIDDEN.http_status
    assert exc.message == Code.FORBIDDEN.message


def test_biz_exception_uses_custom_message():
    exc = BizException(Code.SERVER_ERROR, "upstream failed")

    assert exc.code is Code.SERVER_ERROR
    assert exc.message == "upstream failed"


def test_get_http_exception_code_maps_known_statuses():
    assert get_http_exception_code(StarletteHTTPException(status_code=404)) is Code.NOT_FOUND
    assert get_http_exception_code(StarletteHTTPException(status_code=405)) is Code.METHOD_NOT_ALLOWED
    assert get_http_exception_code(StarletteHTTPException(status_code=418)) is Code.HTTP_ERROR
