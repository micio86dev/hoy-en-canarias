import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from hoy_api.deps import SessionDep

logger = logging.getLogger("hoy_api")
router = APIRouter(tags=["ops"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up. Does not touch the database."""
    return {"status": "ok"}


@router.get("/ready", response_model=None)
def ready(session: SessionDep) -> dict[str, str] | JSONResponse:
    """Readiness: the database answers. Errors are logged server-side, never returned."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("readiness check failed: database unreachable", exc_info=True)
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}
