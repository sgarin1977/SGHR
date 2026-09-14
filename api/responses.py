from typing import Any


def success_envelope(
    *,
    data: Any,
    request_id: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "data": data,
        "meta": dict(meta or {}),
        "request_id": request_id,
    }


def error_envelope(
    *,
    code: str,
    message: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        },
    }
