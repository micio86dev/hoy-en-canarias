from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import respx

from canarias_events.database import Database, UpsertResult
from canarias_events.date_range import TZ, explicit_window
from canarias_events.deduplication import haversine_m, resolve
from canarias_events.models import Event
from canarias_events.sources.base import FetchContext
from canarias_events.sources.ckan import CkanAdapter
from canarias_events.sources.tribe_events import TribeEventsAdapter
from tests.conftest import NOW, fixture_json
from tests.test_database import make_cfg
from tests.test_source_ckan import mock_tenerife, tf_config
from tests.test_source_tribe_events import cfg as tribe_cfg
from tests.test_source_tribe_events import mock_page

DAY = date(2026, 10, 20)


def make_event(**overrides: Any) -> Event:
    data: dict[str, Any] = {
        "title": "Concierto de Navidad",
        "start_datetime": datetime(2026, 10, 20, 20, 0, tzinfo=TZ),
        "end_datetime": datetime(2026, 10, 20, 22, 0, tzinfo=TZ),
        "municipality": "Adeje",
        "island": "Tenerife",
        "source_name": "Source s1",
        "source_event_id": "e1",
        "source_url": "https://s1.test/e1",
    }
    data.update(overrides)
    return Event(**data)


def ingest(
    db: Database,
    event: Event,
    source_id: str = "s1",
    at: datetime = NOW,
    threshold: float = 92.0,
) -> UpsertResult:
    decision = resolve(db, event, source_id, merge_threshold=threshold)
    return db.upsert(event, source_id, decision, at)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "e.sqlite")
    database.sync_sources([make_cfg("s1"), make_cfg("s2"), make_cfg("hi", priority=80)])
    yield database
    database.close()


# --------------------------------------------------------------------------- same source


def test_same_source_rerun_is_idempotent(db: Database) -> None:
    first = ingest(db, make_event())
    second = ingest(db, make_event(), at=NOW + timedelta(hours=1))
    assert (first.outcome, second.outcome) == ("new", "unchanged")
    assert second.event_id == first.event_id
    assert db.count("events") == 1
    assert db.count("event_sources") == 1
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert stored.first_seen_at == NOW
    assert stored.last_seen_at == NOW + timedelta(hours=1)


def test_changed_content_counts_as_updated(db: Database) -> None:
    first = ingest(db, make_event(description="old"))
    second = ingest(db, make_event(description="new text"))
    assert second.outcome == "updated"
    assert second.event_id == first.event_id
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert stored.description == "new text"
    assert ingest(db, make_event(description="new text")).outcome == "unchanged"


def test_events_missing_from_feed_are_never_deleted(db: Database) -> None:
    ingest(db, make_event(source_event_id="a"))
    ingest(db, make_event(source_event_id="b", title="Otra cosa distinta", municipality="Arona"))
    ingest(db, make_event(source_event_id="b", title="Otra cosa distinta", municipality="Arona"))
    assert db.count("events") == 2


def test_same_source_similar_events_are_not_merged(db: Database) -> None:
    ingest(db, make_event(source_event_id="a"))
    second = ingest(db, make_event(source_event_id="b"))
    assert second.outcome == "new"
    assert db.count("events") == 2
    assert second.possible_duplicates == 1
    assert "same source" in db.possible_duplicates()[0]["reason"]


# --------------------------------------------------------------------------- cross source


def test_url_match_merges_across_www_and_tracking(db: Database) -> None:
    first = ingest(db, make_event(official_event_url="https://www.club.test/evento/1/"))
    other = make_event(
        title="Totalmente distinto",
        source_name="Source s2",
        source_event_id="x9",
        official_event_url="https://club.test/evento/1?utm_source=a",
    )
    decision = resolve(db, other, "s2")
    assert decision.kind == "merge"
    assert decision.method == "url"
    result = db.upsert(other, "s2", decision, NOW)
    assert result.outcome == "merged"
    assert result.event_id == first.event_id
    assert db.count("events") == 1
    assert [r["match_method"] for r in db.event_sources(first.event_id)] == ["source_id", "url"]


def test_shared_url_on_another_day_is_not_the_same_event(db: Database) -> None:
    ingest(db, make_event(official_event_url="https://club.test/serie"))
    later = make_event(
        source_name="Source s2", source_event_id="x", official_event_url="https://club.test/serie",
        start_datetime=datetime(2026, 10, 21, 20, 0, tzinfo=TZ),
        end_datetime=datetime(2026, 10, 21, 22, 0, tzinfo=TZ),
    )  # fmt: skip
    assert resolve(db, later, "s2").kind == "new"


def test_fuzzy_merge_same_place_and_time(db: Database) -> None:
    first = ingest(db, make_event())
    other = make_event(
        title="CONCIERTO de Navidad!!", source_name="Source s2", source_event_id="z",
        description="detail",
    )  # fmt: skip
    decision = resolve(db, other, "s2")
    assert (decision.kind, decision.method) == ("merge", "fuzzy")
    result = db.upsert(other, "s2", decision, NOW)
    assert result.outcome == "merged"
    merged = db.get_event(first.event_id)
    assert merged is not None
    assert merged.description == "detail"  # filled from the second source
    assert db.count("event_sources") == 2


def test_similar_title_other_municipality_is_possible_duplicate(db: Database) -> None:
    first = ingest(db, make_event())
    other = make_event(
        source_name="Source s2", source_event_id="z", municipality="Arona",
    )  # fmt: skip
    result = ingest(db, other, "s2")
    assert result.outcome == "new"
    assert result.possible_duplicates == 1
    assert db.count("events") == 2
    row = db.possible_duplicates()[0]
    assert {row["event_id_a"], row["event_id_b"]} == {first.event_id, result.event_id}
    assert "location" in row["reason"]
    # idempotent: a second run does not add another row
    ingest(db, other, "s2")
    assert db.count("possible_duplicates") == 1


def test_mid_score_goes_to_possible_duplicates(db: Database) -> None:
    ingest(db, make_event())
    result = ingest(
        db,
        make_event(title="Gran Concierto de Navidad", source_name="Source s2", source_event_id="z"),
        "s2",
    )
    assert result.outcome == "new"
    assert result.possible_duplicates == 1


def test_clearly_different_titles_are_ignored(db: Database) -> None:
    ingest(db, make_event())
    result = ingest(
        db,
        make_event(title="Torneo de ajedrez", source_name="Source s2", source_event_id="z"),
        "s2",
    )
    assert (result.outcome, result.possible_duplicates) == ("new", 0)


def test_title_with_venue_suffix_merges_across_sources(db: Database) -> None:
    day: dict[str, Any] = {
        "start_datetime": None,
        "end_datetime": None,
        "start_date": date(2026, 10, 21),
        "all_day": True,
        "municipality": "San Cristóbal de La Laguna",
        "venue_name": "Teatro Leal",
    }
    first = ingest(db, make_event(title="Un matrimonio sin filtros", **day))
    other = make_event(
        title="Un matrimonio sin filtros - Teatro Leal de La Laguna",
        source_name="Source s2", source_event_id="z", **day,
    )  # fmt: skip
    decision = resolve(db, other, "s2")
    assert (decision.kind, decision.method, decision.event_id) == (
        "merge", "fuzzy", first.event_id,
    )  # fmt: skip


def test_same_start_with_other_end_date_is_only_a_possible_duplicate(db: Database) -> None:
    ingest(db, make_event(start_datetime=None, end_datetime=None, start_date=date(2026, 10, 21),
                          end_date=date(2026, 10, 22), all_day=True))  # fmt: skip
    result = ingest(
        db,
        make_event(start_datetime=None, end_datetime=None, start_date=date(2026, 10, 21),
                   all_day=True, source_name="Source s2", source_event_id="z"),  # fmt: skip
        "s2",
    )
    assert (result.outcome, result.possible_duplicates) == ("new", 1)
    assert "end date" in db.possible_duplicates()[0]["reason"]


def test_suffix_that_is_not_the_venue_is_kept(db: Database) -> None:
    ingest(db, make_event(venue_name="Teatro Leal"))
    result = ingest(
        db,
        make_event(
            title="Concierto de Navidad - Coro infantil y orquesta juvenil",
            venue_name="Teatro Leal", source_name="Source s2", source_event_id="z",
        ),  # fmt: skip
        "s2",
    )
    assert result.outcome == "new"


def test_different_start_time_is_not_auto_merged(db: Database) -> None:
    ingest(db, make_event())
    other = make_event(
        source_name="Source s2", source_event_id="z",
        start_datetime=datetime(2026, 10, 20, 11, 0, tzinfo=TZ),
        end_datetime=datetime(2026, 10, 20, 13, 0, tzinfo=TZ),
    )  # fmt: skip
    result = ingest(db, other, "s2")
    assert result.outcome == "new"
    assert result.possible_duplicates == 1
    assert "time" in db.possible_duplicates()[0]["reason"]


def test_all_day_side_is_time_compatible(db: Database) -> None:
    ingest(db, make_event())
    other = make_event(
        source_name="Source s2", source_event_id="z", start_datetime=None, end_datetime=None,
        start_date=DAY, end_date=DAY, all_day=True,
    )  # fmt: skip
    assert resolve(db, other, "s2").kind == "merge"


def test_multi_day_requires_same_end_date(db: Database) -> None:
    ingest(db, make_event(end_datetime=datetime(2026, 10, 22, 22, 0, tzinfo=TZ)))
    other = make_event(source_name="Source s2", source_event_id="z")  # ends the same day
    assert resolve(db, other, "s2").kind == "new"
    same = make_event(
        source_name="Source s2", source_event_id="y",
        end_datetime=datetime(2026, 10, 22, 22, 0, tzinfo=TZ),
    )  # fmt: skip
    assert resolve(db, same, "s2").kind == "merge"


def test_coordinates_within_300m_count_as_same_place(db: Database) -> None:
    ingest(db, make_event(municipality=None, latitude=28.1000, longitude=-16.7000))
    near = make_event(
        source_name="Source s2", source_event_id="z", municipality=None,
        latitude=28.1010, longitude=-16.7000,
    )  # fmt: skip
    far = make_event(
        source_name="Source s2", source_event_id="y", municipality=None,
        latitude=28.1100, longitude=-16.7000,
    )  # fmt: skip
    assert resolve(db, near, "s2").kind == "merge"
    assert resolve(db, far, "s2").kind == "new"
    assert 100 < haversine_m(28.1, -16.7, 28.101, -16.7) < 120


def test_venue_similarity_counts_as_same_place(db: Database) -> None:
    ingest(db, make_event(municipality=None, venue_name="Auditorio de Adeje"))
    other = make_event(
        source_name="Source s2", source_event_id="z", municipality=None,
        venue_name="auditorio de adeje ",
    )  # fmt: skip
    assert resolve(db, other, "s2").kind == "merge"


def test_configurable_merge_threshold(db: Database) -> None:
    ingest(db, make_event())
    other = make_event(
        title="Gran Concierto de Navidad", source_name="Source s2", source_event_id="z"
    )
    assert resolve(db, other, "s2").kind == "new"
    assert resolve(db, other, "s2", merge_threshold=85).kind == "merge"


# --------------------------------------------------------------------------- merge rules


def test_merge_never_overwrites_with_null(db: Database) -> None:
    first = ingest(db, make_event(description="keep me", image_url="https://i.test/a.png"))
    ingest(db, make_event(source_name="Source s2", source_event_id="z", description=None), "s2")
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert stored.description == "keep me"
    assert stored.image_url == "https://i.test/a.png"


def test_same_source_update_can_change_value_but_not_blank_it(db: Database) -> None:
    first = ingest(db, make_event(description="v1", venue_name="Sala A"))
    ingest(db, make_event(description="v2", venue_name=None))
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert (stored.description, stored.venue_name) == ("v2", "Sala A")


def test_lower_priority_does_not_overwrite_existing_value(db: Database) -> None:
    first = ingest(db, make_event(source_event_id="h1", description="official text"), "hi")
    other = make_event(
        source_name="Source s2", source_event_id="z", description="aggregator text",
        venue_name="Sala B",
    )  # fmt: skip
    ingest(db, other, "s2")
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert stored.description == "official text"
    assert stored.venue_name == "Sala B"  # fills a gap


def test_higher_priority_overwrites_and_provenance_is_recorded(db: Database) -> None:
    first = ingest(db, make_event(description="aggregator text"), "s1")
    other = make_event(
        source_name="Source hi", source_event_id="h1", description="official text",
    )  # fmt: skip
    ingest(db, other, "hi")
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert stored.description == "official text"
    provenance = _provenance(db, first.event_id)
    assert provenance["description"] == "hi"
    assert provenance["title"] == "s1"
    link = {r["source_id"]: r for r in db.event_sources(first.event_id)}
    assert "description" in link["hi"]["field_provenance"]
    assert "description" not in link["s1"]["field_provenance"]


def test_provenance_preserved_when_lower_source_merges_later(db: Database) -> None:
    first = ingest(db, make_event(source_event_id="h1", description="official"), "hi")
    ingest(db, make_event(source_name="Source s2", source_event_id="z", description="other"), "s2")
    assert _provenance(db, first.event_id)["description"] == "hi"


def test_tags_are_unioned_and_extra_merged(db: Database) -> None:
    first = ingest(db, make_event(tags=["music"], extra={"a": 1}))
    ingest(
        db,
        make_event(source_name="Source s2", source_event_id="z", tags=["live"], extra={"b": 2}),
        "s2",
    )
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert stored.tags == ["music", "live"]
    assert stored.extra == {"a": 1, "b": 2}


def test_time_group_is_taken_together(db: Database) -> None:
    all_day = make_event(
        start_datetime=None, end_datetime=None, start_date=DAY, end_date=DAY, all_day=True
    )
    first = ingest(db, all_day)
    ingest(db, make_event(source_name="Source s2", source_event_id="z"), "s2")
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert stored.start_datetime == datetime(2026, 10, 20, 20, 0, tzinfo=TZ)
    assert stored.end_datetime == datetime(2026, 10, 20, 22, 0, tzinfo=TZ)
    assert stored.all_day is False


def test_stable_event_id_across_runs(db: Database) -> None:
    ids = {ingest(db, make_event(description=f"v{i}")).event_id for i in range(3)}
    assert len(ids) == 1


def test_events_without_source_event_id_still_dedupe_by_content_key(db: Database) -> None:
    first = ingest(db, make_event(source_event_id=None))
    again = ingest(db, make_event(source_event_id=None))
    assert (first.outcome, again.outcome) == ("new", "unchanged")


def _provenance(db: Database, event_id: str) -> dict[str, str]:
    import json

    row = db.conn.execute(
        "SELECT field_provenance FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    return dict(json.loads(row[0]))


# --------------------------------------------------------------------------- real fixtures


def _fixture_events() -> tuple[list[Event], list[Event]]:
    wide = explicit_window(date(2000, 1, 1), date(2100, 1, 1))
    import logging

    from canarias_events.http import SafeHttpClient
    from tests.conftest import _public_resolver

    def ctx() -> FetchContext:
        client = SafeHttpClient(
            resolver=_public_resolver, min_interval=0, retries=0, sleep=lambda _s: None
        )
        return FetchContext(
            client=client, window=wide, now=NOW, logger=logging.getLogger("t"), env={}
        )

    with respx.mock:
        mock_tenerife()
        ckan = CkanAdapter(tf_config()).fetch(ctx()).events
        payload = fixture_json("deportestenerife_tribe.json")
        payload.pop("next_rest_url")
        mock_page("deportestenerife.es", payload)
        tribe = TribeEventsAdapter(tribe_cfg("deportestenerife.es", id="tribe")).fetch(ctx()).events
    return ckan, tribe


def test_tenerife_fixtures_share_no_events_but_ckan_repeats_one_across_www(
    tmp_path: Path,
) -> None:
    """The captured Tribe and CKAN samples cover different days: nothing may be merged.

    The CKAN sample does contain one event twice (www and bare host, different venue text):
    same source, so it is reported as a possible duplicate and never merged.
    """
    ckan, tribe = _fixture_events()
    assert not {e.start_date for e in ckan} & {e.start_date for e in tribe}
    with Database(tmp_path / "f.sqlite") as db:
        db.sync_sources([make_cfg("tribe"), make_cfg("ckan")])
        outcomes = [ingest(db, e, "tribe").outcome for e in tribe]
        outcomes += [ingest(db, e, "ckan").outcome for e in ckan]
        assert "merged" not in outcomes
        assert db.count("events") == len(tribe) + len(ckan)
        assert db.count("possible_duplicates") == 1
        assert "same source" in db.possible_duplicates()[0]["reason"]


def test_real_ckan_event_merges_with_a_tribe_style_copy(db: Database) -> None:
    ckan, _ = _fixture_events()
    original = next(e for e in ckan if e.title.startswith("Tercera jornada de Paddle Surf"))
    assert original.official_event_url is not None
    first = ingest(db, original.model_copy(update={"source_name": "CKAN"}), "s1")
    tribe_copy = original.model_copy(
        update={
            "source_name": "Tribe",
            "source_event_id": "tribe-991",
            "official_event_url": None,  # force the fuzzy path
            "title": original.title.replace("Tercera", "tercera"),
            "description": "From the Tribe feed",
        }
    )
    result = ingest(db, tribe_copy, "s2")
    assert (result.outcome, result.event_id) == ("merged", first.event_id)
    assert db.count("events") == 1


def test_half_a_coordinate_pair_never_replaces_a_complete_one(db: Database) -> None:
    first = ingest(db, make_event(source_event_id="h1", latitude=28.1, longitude=-16.7), "s1")
    half = Event.model_construct(
        **{
            **make_event(source_name="Source hi", source_event_id="z").model_dump(),
            "latitude": 28.5,
        }
    )
    db.upsert(half, "hi", resolve(db, half, "hi"), NOW)
    stored = db.get_event(first.event_id)
    assert stored is not None
    assert (stored.latitude, stored.longitude) == (28.1, -16.7)


# --------------------------------------------------------------------------- social trust


@pytest.fixture
def social_db(db: Database) -> Database:
    db.sync_sources([make_cfg("soc", priority=10)])
    return db


def social_event(**overrides: Any) -> Event:
    data: dict[str, Any] = {
        "source_name": "Source soc",
        "source_event_id": "post-1",
        "extra": {"source_trust": "social", "post_url": "https://social.test/1"},
    }
    data.update(overrides)
    return make_event(**data)


def test_a_social_event_is_never_fuzzy_merged_into_a_reliable_one(social_db: Database) -> None:
    reliable = ingest(social_db, make_event(), "s1")
    result = ingest(social_db, social_event(title="Concierto de Navidad!"), "soc")

    assert result.outcome == "new" and result.event_id != reliable.event_id
    assert result.possible_duplicates == 1
    [pair] = social_db.possible_duplicates()
    assert "social" in pair["reason"]
    assert social_db.count("events") == 2


def test_a_reliable_event_is_not_fuzzy_merged_into_a_social_one_either(
    social_db: Database,
) -> None:
    ingest(social_db, social_event(), "soc")
    result = ingest(social_db, make_event(), "s1")
    assert result.outcome == "new" and result.possible_duplicates == 1


def test_two_social_sources_are_never_merged_by_fuzzy_match(social_db: Database) -> None:
    social_db.sync_sources([make_cfg("soc2", priority=10)])
    ingest(social_db, social_event(), "soc")
    result = ingest(social_db, social_event(source_event_id="post-2"), "soc2")
    assert result.outcome == "new" and social_db.count("events") == 2


def test_the_same_official_url_still_merges_and_social_never_overwrites(
    social_db: Database,
) -> None:
    url = "https://teatro.test/evento/gala"
    reliable = ingest(
        social_db, make_event(official_event_url=url, description="Texto oficial"), "s1"
    )
    result = ingest(
        social_db,
        social_event(official_event_url=url, description="Tweet del fan", image_url="https://i/x"),
        "soc",
    )

    assert result.outcome == "merged" and result.event_id == reliable.event_id
    stored = social_db.get_event(reliable.event_id)
    assert stored is not None
    assert stored.description == "Texto oficial"  # the reliable value stays
    assert stored.title == "Concierto de Navidad"
    assert stored.image_url == "https://i/x"  # a gap may be filled; nothing is overwritten


def test_a_reliable_source_overrides_what_a_social_one_wrote_first(social_db: Database) -> None:
    url = "https://teatro.test/evento/gala"
    first = ingest(social_db, social_event(official_event_url=url, description="Tweet"), "soc")
    ingest(social_db, make_event(official_event_url=url, description="Texto oficial"), "s1")
    stored = social_db.get_event(first.event_id)
    assert stored is not None and stored.description == "Texto oficial"
