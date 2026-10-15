from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from canarias_events.date_range import TZ
from canarias_events.models import Event


def make(**kw: object) -> Event:
    data: dict[str, object] = {"title": "Concierto", "source_name": "test"}
    data.update(kw)
    return Event.model_validate(data)


def test_minimal_event_defaults() -> None:
    e = make()
    assert e.tags == [] and e.extra == {} and e.raw_data == {}
    assert e.all_day is False and e.timezone == "Atlantic/Canary"
    assert e.start_datetime is None and e.latitude is None and e.is_free is None


def test_all_spec_fields_exist() -> None:
    expected = {
        "id", "title", "description", "summary", "category", "tags", "start_datetime",
        "end_datetime", "start_date", "end_date", "timezone", "all_day", "venue_name", "address",
        "municipality", "island", "postal_code", "latitude", "longitude", "price_min",
        "price_max", "currency", "is_free", "booking_url", "ticket_url", "official_event_url",
        "image_url", "organizer_name", "organizer_url", "contact_email", "source_name",
        "source_url", "source_event_id", "source_type", "source_license", "first_seen_at",
        "last_seen_at", "imported_at", "last_verified_at", "raw_data", "extra",
    }  # fmt: skip
    assert expected == set(Event.model_fields)


def test_title_stripped_and_required() -> None:
    assert make(title="  Hola  ").title == "Hola"
    with pytest.raises(ValidationError):
        make(title="   ")
    with pytest.raises(ValidationError):
        make(title="")
    with pytest.raises(ValidationError):
        Event.model_validate({"source_name": "x"})


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x", "ftp://a.test/x",
     "file:///etc/passwd", "//a.test/x", "/relative", "not a url", "", "   ", "http://"],
)  # fmt: skip
def test_unsafe_urls_become_none(url: str) -> None:
    e = make(
        booking_url=url, ticket_url=url, official_event_url=url, image_url=url,
        organizer_url=url, source_url=url,
    )  # fmt: skip
    for f in ("booking_url", "ticket_url", "official_event_url", "image_url", "organizer_url",
              "source_url"):  # fmt: skip
        assert getattr(e, f) is None


def test_http_urls_kept() -> None:
    assert make(booking_url=" https://a.test/x?y=1 ").booking_url == "https://a.test/x?y=1"
    assert make(source_url="http://a.test").source_url == "http://a.test"


@pytest.mark.parametrize("lat", [-90.1, 90.1, 1000])
def test_latitude_out_of_range_rejected(lat: float) -> None:
    with pytest.raises(ValidationError):
        make(latitude=lat)


@pytest.mark.parametrize("lon", [-180.1, 180.1, 999])
def test_longitude_out_of_range_rejected(lon: float) -> None:
    with pytest.raises(ValidationError):
        make(longitude=lon)


def test_coordinates_boundaries_and_nan() -> None:
    e = make(latitude=90, longitude=-180)
    assert e.latitude == 90 and e.longitude == -180
    with pytest.raises(ValidationError):
        make(latitude=float("nan"))


def test_blank_strings_become_none() -> None:
    e = make(description="  ", venue_name="", municipality=" La Laguna ")
    assert e.description is None and e.venue_name is None and e.municipality == "La Laguna"


def test_naive_datetime_assumed_canary() -> None:
    e = make(start_datetime=datetime(2026, 10, 19, 20, 0))
    assert e.start_datetime == datetime(2026, 10, 19, 20, 0, tzinfo=TZ)
    assert e.start_datetime is not None and e.start_datetime.tzinfo is not None


def test_dates_derived_from_datetimes_in_event_timezone() -> None:
    e = make(
        start_datetime=datetime(2026, 10, 19, 23, 30, tzinfo=UTC),
        end_datetime=datetime(2026, 10, 20, 2, 0, tzinfo=UTC),
    )
    assert e.start_date == date(2026, 10, 20)  # 00:30 local (UTC+1)
    assert e.end_date == date(2026, 10, 20)


def test_explicit_dates_not_overridden() -> None:
    e = make(start_datetime=datetime(2026, 10, 19, 20, 0, tzinfo=TZ), start_date=date(2026, 10, 1))
    assert e.start_date == date(2026, 10, 1)


def test_all_day_event_with_dates_only() -> None:
    e = make(all_day=True, start_date=date(2026, 10, 25))
    assert e.start_datetime is None and e.start_date == date(2026, 10, 25)


# --------------------------------------------------------------------------- content hash


def test_content_hash_stable_and_ignores_volatile_fields() -> None:
    a = make(
        start_datetime=datetime(2026, 10, 19, 20, 0, tzinfo=TZ),
        tags=["a", "b"],
        raw_data={"x": 1},
        first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 1, 2, tzinfo=UTC),
        imported_at=datetime(2026, 1, 3, tzinfo=UTC),
        last_verified_at=datetime(2026, 1, 4, tzinfo=UTC),
        id="abc",
    )
    b = make(
        start_datetime=datetime(2026, 10, 19, 20, 0, tzinfo=TZ),
        tags=["b", "a"],
        raw_data={"y": 2, "other": [1, 2, 3]},
        first_seen_at=datetime(2027, 1, 1, tzinfo=UTC),
        last_seen_at=datetime(2027, 1, 2, tzinfo=UTC),
        imported_at=None,
        id="zzz",
    )
    assert a.content_hash() == b.content_hash()
    assert len(a.content_hash()) == 64
    assert a.content_hash() == a.content_hash()


def test_content_hash_same_instant_different_timezone_representation() -> None:
    a = make(start_datetime=datetime(2026, 10, 19, 20, 0, tzinfo=TZ))
    b = make(start_datetime=datetime(2026, 10, 19, 21, 0, tzinfo=ZoneInfo("Europe/Madrid")))
    assert a.content_hash() == b.content_hash()


@pytest.mark.parametrize(
    "change",
    [
        {"title": "Otro"},
        {"description": "x"},
        {"venue_name": "Teatro"},
        {"price_min": 5.0},
        {"island": "Tenerife"},
        {"extra": {"age": "18+"}},
        {"booking_url": "https://a.test/x"},
        {"start_datetime": datetime(2026, 10, 19, 21, 0, tzinfo=TZ)},
        {"latitude": 28.4},
    ],
)
def test_content_hash_changes_with_meaningful_fields(change: dict[str, object]) -> None:
    base = make(start_datetime=datetime(2026, 10, 19, 20, 0, tzinfo=TZ))
    assert (
        base.content_hash()
        != make(
            **{"start_datetime": datetime(2026, 10, 19, 20, 0, tzinfo=TZ), **change}
        ).content_hash()
    )


def test_content_hash_ignores_whitespace_noise_in_text() -> None:
    assert make(description="a   b\n c").content_hash() == make(description="a b c").content_hash()
