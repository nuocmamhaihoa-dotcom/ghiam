from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.crypto import SecretDecryptionError
from app.errors import AppError

MAX_REPORTED_ERRORS = 5

_TEMPLATES: dict[str, str] = {
    "missing": "bắt buộc phải có",
    "string_too_short": "phải có ít nhất {min_length} ký tự",
    "string_too_long": "tối đa {max_length} ký tự",
    "string_pattern_mismatch": "sai định dạng",
    "greater_than_equal": "phải lớn hơn hoặc bằng {ge}",
    "less_than_equal": "phải nhỏ hơn hoặc bằng {le}",
    "greater_than": "phải lớn hơn {gt}",
    "less_than": "phải nhỏ hơn {lt}",
    "int_parsing": "phải là số nguyên",
    "int_type": "phải là số nguyên",
    "int_from_float": "phải là số nguyên",
    "float_parsing": "phải là số",
    "bool_parsing": "phải là true hoặc false",
    "bool_type": "phải là true hoặc false",
    "string_type": "phải là chuỗi ký tự",
    "list_type": "phải là danh sách",
    "too_long": "tối đa {max_length} phần tử",
    "too_short": "phải có ít nhất {min_length} phần tử",
    "literal_error": "chỉ nhận một trong các giá trị: {expected}",
    "enum": "chỉ nhận một trong các giá trị: {expected}",
    "json_invalid": "JSON không hợp lệ",
    "model_attributes_type": "phải là object JSON",
    "dict_type": "phải là object JSON",
    "extra_forbidden": "không được phép có",
}


def _field_path(loc: Sequence[Any]) -> str:
    parts = [str(part) for part in loc]
    if parts and parts[0] in ("body", "query", "path", "header"):
        parts = parts[1:]
    return ".".join(parts) or "dữ liệu"


def describe_validation_error(error: Mapping[str, Any]) -> str:
    template = _TEMPLATES.get(str(error.get("type")))
    context = error.get("ctx") or {}
    message = str(error.get("msg", "không hợp lệ"))
    if template is not None:
        try:
            message = template.format(**context)
        except (KeyError, IndexError):
            message = template
    return f"{_field_path(error.get('loc', ()))}: {message}"


async def _app_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


async def _secret_error(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = list(exc.errors())
    messages = [describe_validation_error(error) for error in errors[:MAX_REPORTED_ERRORS]]
    if len(errors) > MAX_REPORTED_ERRORS:
        messages.append(f"và {len(errors) - MAX_REPORTED_ERRORS} lỗi khác")
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Dữ liệu không hợp lệ: " + "; ".join(messages),
            "errors": [
                {
                    "loc": [str(part) for part in error.get("loc", ())],
                    "type": error.get("type"),
                    "msg": error.get("msg"),
                }
                for error in errors
            ],
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(SecretDecryptionError, _secret_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
