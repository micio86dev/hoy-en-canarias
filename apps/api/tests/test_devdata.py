from datetime import date, timedelta

from hoy_api.devdata import demo_events

from canarias_events.models import Event as ImportedEvent


def _events(anchor: date) -> list[ImportedEvent]:
    return [event for _, event in demo_events(anchor)]


def test_demo_dates_are_relative_to_the_anchor_day() -> None:
    anchor = date(2026, 10, 16)
    jazz = next(e for e in _events(anchor) if e.title == "Concierto de jazz en el puerto")

    assert jazz.start_datetime is not None
    assert jazz.start_datetime.date() == anchor + timedelta(days=5)


def test_demo_set_covers_free_paid_mapped_and_past_events() -> None:
    anchor = date(2026, 10, 16)
    events = _events(anchor)

    assert any(e.is_free is True for e in events)
    assert any(e.price_min is not None for e in events)
    assert any(e.latitude is not None for e in events)
    assert any(e.start_datetime is not None and e.start_datetime.date() < anchor for e in events)
