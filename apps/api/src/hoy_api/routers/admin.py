import secrets
import time
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from hoy_api.importer import run_configured_cycle

router = APIRouter(prefix="/admin", tags=["admin"])
BEARER_PREFIX = "Bearer "


def _run_manual_import(app: FastAPI) -> None:
    state = app.state
    run_configured_cycle(
        session_factory=state.session_factory,
        sqlite_path=state.settings.importer_db_path,
        runner=state.run_importer,
        window_days=state.settings.import_window_days,
        trigger="manual",
        now=state.clock,
        sleep=time.sleep,
        max_attempts=state.settings.import_max_attempts,
        backoff_seconds=state.settings.import_retry_backoff_seconds,
    )


@router.post("/import", status_code=202, response_model=None)
def trigger_import(
    request: Request,
    background: BackgroundTasks,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Start an importer cycle in the background. Disabled unless HOY_ADMIN_TOKEN is set."""
    token = request.app.state.settings.admin_token
    if token is None:
        raise HTTPException(status_code=404, detail="Not found")
    presented = ""
    if authorization is not None and authorization.startswith(BEARER_PREFIX):
        presented = authorization[len(BEARER_PREFIX) :]
    if not secrets.compare_digest(presented.encode(), token.get_secret_value().encode()):
        raise HTTPException(
            status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Bearer"}
        )
    background.add_task(_run_manual_import, request.app)
    return JSONResponse({"status": "accepted"}, status_code=202)
