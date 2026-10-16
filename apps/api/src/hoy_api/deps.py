import math
import secrets
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session, sessionmaker

from hoy_api.ratelimit import TokenBucketLimiter


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def get_clock(request: Request) -> datetime:
    clock: Callable[[], datetime] = request.app.state.clock
    return clock()


def enforce_rate_limit(request: Request) -> None:
    limiter: TokenBucketLimiter = request.app.state.rate_limiter
    client = request.client.host if request.client else "unknown"
    allowed, retry_after = limiter.hit(client)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


SessionDep = Annotated[Session, Depends(get_session)]
ClockDep = Annotated[datetime, Depends(get_clock)]


def require_admin(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Bearer-token guard for moderation routes. Disabled (404) unless a token is configured."""
    token = request.app.state.settings.admin_token
    if token is None:
        raise HTTPException(status_code=404, detail="Not found")
    presented = ""
    if authorization is not None and authorization.startswith("Bearer "):
        presented = authorization[len("Bearer ") :]
    if not secrets.compare_digest(presented.encode(), token.get_secret_value().encode()):
        raise HTTPException(
            status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Bearer"}
        )
