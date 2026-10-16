from typing import Annotated

from fastapi import APIRouter, Depends, Query

from hoy_api.deps import ClockDep, SessionDep, enforce_rate_limit
from hoy_api.repositories.events import category_counts, island_counts, municipality_counts
from hoy_api.schemas import CountItem, CountList
from hoy_api.time_windows import local_today

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


@router.get("/islands", response_model=CountList, tags=["catalog"])
def islands(session: SessionDep, now: ClockDep) -> CountList:
    counts = island_counts(session, local_today(now))
    return CountList(items=[CountItem(key=name, count=count) for name, count in counts.items()])


@router.get("/categories", response_model=CountList, tags=["catalog"])
def categories(session: SessionDep, now: ClockDep) -> CountList:
    counts = category_counts(session, local_today(now))
    return CountList(items=[CountItem(key=name, count=count) for name, count in counts.items()])


@router.get("/municipalities", response_model=CountList, tags=["catalog"])
def municipalities(
    session: SessionDep,
    now: ClockDep,
    island: Annotated[str | None, Query(max_length=40)] = None,
) -> CountList:
    rows = municipality_counts(session, local_today(now), island)
    return CountList(items=[CountItem(key=name, count=count) for name, count in rows])
