import math
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
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
