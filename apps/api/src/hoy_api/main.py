from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, sessionmaker

from canarias_events.http import SafeHttpClient
from hoy_api.db.base import make_engine, make_session_factory
from hoy_api.errors import install_error_handlers
from hoy_api.importer import subprocess_runner
from hoy_api.middleware import add_security_headers
from hoy_api.moderation.jev import JevModerator
from hoy_api.ratelimit import TokenBucketLimiter
from hoy_api.routers import admin, catalog, events, ops, sitemap, submissions
from hoy_api.settings import Settings, get_settings


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    clock: Callable[[], datetime] = _utc_now,
    moderator: Any = None,
) -> FastAPI:
    config = settings or get_settings()
    app = FastAPI(
        title="Hoy en Canarias API",
        version="0.1.0",
        description="Public, read-only discovery API for events in the Canary Islands.",
    )
    app.state.settings = config
    app.state.session_factory = session_factory or make_session_factory(
        make_engine(config.database_url.get_secret_value())
    )
    app.state.clock = clock
    key = config.typesafe_api_key.get_secret_value() if config.typesafe_api_key else None
    app.state.moderator = moderator or JevModerator(SafeHttpClient(), key)
    app.state.rate_limiter = TokenBucketLimiter(per_minute=config.rate_limit_per_minute)
    app.state.run_importer = subprocess_runner(
        config.importer_command, config.importer_db_path, config.importer_timeout_seconds
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    app.middleware("http")(add_security_headers)
    install_error_handlers(app)

    app.include_router(ops.router)
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(catalog.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(sitemap.router, prefix="/api/v1")
    app.include_router(submissions.public, prefix="/api/v1")
    app.include_router(submissions.admin, prefix="/api/v1")
    return app


app = create_app()
