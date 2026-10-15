"""Social source registry: platform rows, acquisition modes and what may be scheduled."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from canarias_events.social import (
    NOT_ACQUIRABLE,
    SocialRegistry,
    SocialSource,
    load_social_registry,
    schedule_blocker,
    scheduled_sources,
)

SHIPPED = Path(__file__).parent.parent / "config" / "social_sources.yaml"


def account(**overrides: Any) -> SocialSource:
    data: dict[str, Any] = {
        "id": "mastodon_demo",
        "platform": "mastodon",
        "name": "Demo account",
        "kind": "account",
        "adapter": "mastodon",
        "handle": "agenda@mastodon.example",
        "url": "https://mastodon.example/@agenda",
        "acquisition_mode": "rss",
        "verification_state": "verified",
        "enabled": True,
        "frequency": "daily",
        "islands": ["Tenerife"],
    }
    data.update(overrides)
    return SocialSource.model_validate(data)


# --------------------------------------------------------------------------- shipped file


def test_shipped_registry_documents_the_verified_platforms() -> None:
    registry = load_social_registry(SHIPPED)
    by_id = {s.id: s for s in registry.sources}
    assert len(by_id) == len(registry.sources)
    expected = {
        "facebook_pages": "requires_authorization",
        "facebook_groups": "excluded",
        "instagram": "requires_authorization",
        "meetup": "requires_authorization",
        "eventbrite_api": "excluded",
        "eventbrite_pages": "import_url",
        "telegram": "manual_only",
        "youtube_channel_rss": "rss",
        "mastodon_rss": "rss",
        "bluesky_public_api": "api_public",
        "radio_insular_feed": "rss",
    }
    assert {i: by_id[i].acquisition_mode for i in expected} == expected
    radio = by_id["radio_insular_feed"]
    assert radio.url == "https://radioinsular.es/feed/"
    assert radio.verification_state == "candidate"


def test_shipped_registry_schedules_nothing_and_invents_no_accounts() -> None:
    registry = load_social_registry(SHIPPED)
    assert scheduled_sources(registry) == []
    assert not any(s.enabled for s in registry.sources)
    accounts = [s for s in registry.sources if s.kind == "account"]
    assert [a.id for a in accounts] == ["radio_insular_feed"]  # only the documented candidate


def test_every_shipped_row_has_a_check_date() -> None:
    for source in load_social_registry(SHIPPED).sources:
        assert source.last_checked == date(2026, 10, 15), source.id


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize("mode", sorted(NOT_ACQUIRABLE))
def test_non_acquirable_modes_cannot_be_enabled_or_scheduled(mode: str) -> None:
    with pytest.raises(ValidationError, match="never scheduled"):
        account(acquisition_mode=mode)  # enabled + frequency in the helper
    row = account(acquisition_mode=mode, enabled=False, frequency=None)
    assert schedule_blocker(row) is not None


def test_unknown_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        account(acquisition_mode="scrape")
    with pytest.raises(ValidationError):
        account(verification_state="trusted")
    with pytest.raises(ValidationError):
        account(frequency="sometimes")
    with pytest.raises(ValidationError):
        account(url="ftp://mastodon.example/@a")
    with pytest.raises(ValidationError):
        account(surprise="field")


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    row = {"id": "a", "platform": "web", "name": "A", "acquisition_mode": "rss"}
    path = tmp_path / "social.yaml"
    path.write_text(yaml.safe_dump({"sources": [row, row]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_social_registry(path)


def test_missing_file_is_an_empty_registry(tmp_path: Path) -> None:
    assert load_social_registry(tmp_path / "nope.yaml") == SocialRegistry()


# --------------------------------------------------------------------------- scheduling


def test_a_verified_enabled_account_is_scheduled_as_a_low_priority_source() -> None:
    [cfg] = scheduled_sources(SocialRegistry(sources=[account()]))
    assert cfg.id == "mastodon_demo" and cfg.adapter == "mastodon"
    assert cfg.url == "https://mastodon.example/@agenda.rss"
    assert cfg.frequency == "daily" and cfg.islands == ["Tenerife"]
    assert cfg.priority < 50  # never outranks a reliable source
    assert cfg.params["handle"] == "agenda@mastodon.example"


def test_a_bluesky_account_uses_the_public_appview() -> None:
    row = account(
        id="bsky_demo", platform="bluesky", adapter="bluesky", handle="agenda.bsky.social",
        acquisition_mode="api_public", url=None,
    )  # fmt: skip
    [cfg] = scheduled_sources(SocialRegistry(sources=[row]))
    assert cfg.adapter == "bluesky"
    assert cfg.url == "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    assert cfg.params["handle"] == "agenda.bsky.social"


@pytest.mark.parametrize(
    "overrides",
    [
        {"verification_state": "candidate"},
        {"verification_state": "rejected"},
        {"enabled": False},
        {"kind": "platform"},
        {"acquisition_mode": "import_url"},
        {"adapter": None},
    ],
)
def test_everything_else_is_not_scheduled(overrides: dict[str, Any]) -> None:
    row = account(**overrides)
    assert schedule_blocker(row) is not None
    assert scheduled_sources(SocialRegistry(sources=[row])) == []


def test_link_hosts_and_year_policy_reach_the_adapter_params() -> None:
    row = account(link_hosts=["Teatro.Example.org"], year_policy="infer")
    [cfg] = scheduled_sources(SocialRegistry(sources=[row]))
    assert cfg.params == {
        "handle": "agenda@mastodon.example", "link_hosts": ["teatro.example.org"],
        "year_policy": "infer",
    }  # fmt: skip
    assert account().year_policy == "strict" and account().link_hosts == []


@pytest.mark.parametrize("host", ["https://teatro.example.org", "teatro.example.org/agenda", "x"])
def test_link_hosts_must_be_bare_host_names(host: str) -> None:
    with pytest.raises(ValidationError, match="link_hosts"):
        account(link_hosts=[host])


def test_a_malformed_handle_is_rejected() -> None:
    with pytest.raises(ValidationError, match="handle"):
        account(handle="not a handle")
    with pytest.raises(ValidationError, match="handle"):
        account(platform="bluesky", adapter="bluesky", handle="agenda@mastodon.example")
