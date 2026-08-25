from __future__ import annotations

from typing import Any


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def ok(operation: str, message: str, **data: Any) -> dict[str, Any]:
    return {"success": True, "operation": operation, "data": data, "message": message}


def failure(operation: str, error: ServiceError) -> dict[str, Any]:
    return {
        "success": False,
        "operation": operation,
        "error_code": error.code,
        "message": error.message,
        **error.details,
    }
