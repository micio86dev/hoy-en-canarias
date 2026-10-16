import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("hoy_api")

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    404: "not_found",
    405: "method_not_allowed",
    429: "rate_limited",
    503: "unavailable",
}


def error_body(
    code: str, message: str, details: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return body


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            error_body(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Only location and message are echoed back: never the submitted values.
        details = [
            {
                "field": ".".join(str(part) for part in err["loc"][1:]) or "query",
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            error_body("validation_error", "Invalid request", details), status_code=422
        )

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", exc_info=exc)
        return JSONResponse(error_body("internal_error", "Internal server error"), status_code=500)
