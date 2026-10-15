from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import httpx
import pytest
import respx

from canarias_events.date_range import TZ
from canarias_events.http import HttpError, InvalidPayload, UnsafeUrlError
from canarias_events.sources.base import FetchContext, SourceConfig
from canarias_events.sources.ckan import CkanAdapter
from tests.conftest import fixture_bytes, fixture_json

MakeCtx = Callable[..., FetchContext]

TF_URL = "https://datos.tenerife.es/ckan/api/3/action/package_show?id=agenda-de-eventos-deportivos-en-tenerife"
TF_JSON = (
    "https://datos.tenerife.es/ckan/dataset/6cde1436-8d6f-4cf6-b5a5-c32c8a738478/resource/"
    "de1231c0-957a-4ad0-8b7b-19d6b1bfced6/download/agenda-de-eventos-deportivos-en-tenerife.json"
)
GC_URL = "https://datos.canarias.es/catalogos/general/api/3/action/package_show?id=agenda-cultural"
GC_CSV = (
    "https://datos.canarias.es/catalogos/general/dataset/11dc9d88-b456-4ae2-bcf9-07ad624fd025/"
    "resource/d179079c-a2ed-40dc-a748-7b64d093c342/download/agendaproxima.csv"
)


def tf_config(**overrides: Any) -> SourceConfig:
    data: dict[str, Any] = {
        "id": "cabildo_tenerife_deportes",
        "name": "Cabildo de Tenerife - agenda deportiva",
        "adapter": "ckan",
        "url": TF_URL,
        "islands": ["Tenerife"],
        "license": "CC-BY",
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def gc_config(**overrides: Any) -> SourceConfig:
    data: dict[str, Any] = {
        "id": "gobcan_agenda_cultural",
        "name": "Gobierno de Canarias - agenda cultural",
        "adapter": "ckan",
        "url": GC_URL,
        "license": "gobcan-aviso-legal",
        "params": {
            "profile": "gobcan_agenda",
            "resource_id": "d179079c-a2ed-40dc-a748-7b64d093c342",
        },
    }
    data.update(overrides)
    return SourceConfig.model_validate(data)


def mock_tenerife(events: Any = None, package: Any = None) -> tuple[respx.Route, respx.Route]:
    package_route = respx.get(TF_URL).mock(
        return_value=httpx.Response(
            200, json=package or fixture_json("ckan_package_show_tenerife_sports.json")
        )
    )
    data_route = respx.get(TF_JSON).mock(
        return_value=httpx.Response(200, json=events or fixture_json("tenerife_sports_events.json"))
    )
    return package_route, data_route


def mock_gobcan(csv_bytes: bytes | None = None) -> respx.Route:
    respx.get(GC_URL).mock(
        return_value=httpx.Response(200, json=fixture_json("ckan_package_show_gobcan_agenda.json"))
    )
    return respx.get(GC_CSV).mock(
        return_value=httpx.Response(
            200, content=csv_bytes or fixture_bytes("gobcan_agenda_proxima.csv")
        )
    )


# --------------------------------------------------------------------------- JSON (Tenerife)


@respx.mock
def test_maps_tenerife_sports_json(make_ctx: MakeCtx) -> None:
    _, data_route = mock_tenerife()
    config = tf_config()

    result = CkanAdapter(config).fetch(make_ctx())

    assert data_route.call_count == 1  # the data dictionary resource is never downloaded
    assert result.records_received == 7 and len(result.events) == 7 and result.pages == 1
    assert result.discarded == [] and result.warnings == []
    first = result.events[0]
    assert (
        first.title
        == "Jornada Mujeres y Deporte 2026 | Iniciación al Senderismo y la Carrera de Montaña"
    )
    assert first.start_datetime == datetime(2026, 10, 24, 9, 0, tzinfo=TZ)
    assert first.end_datetime == datetime(2026, 10, 24, 13, 0, tzinfo=TZ)
    assert first.all_day is False
    assert first.venue_name == "Las Lagunetas"
    assert first.municipality == "El Rosario" and first.island == "Tenerife"
    assert first.organizer_name == "Cabildo de Tenerife"
    assert first.official_event_url and first.official_event_url.startswith(
        "https://deportestenerife.es/"
    )
    assert first.source_url == config.url
    assert first.source_license == "CC-BY"
    assert first.source_type == "ckan"
    assert first.source_event_id and re.fullmatch(r"[0-9a-f]{16}", first.source_event_id)
    assert first.raw_data["evento_nombre"] == first.title


@respx.mock
def test_placeholders_and_numeric_organizer_become_none(make_ctx: MakeCtx) -> None:
    mock_tenerife()

    events = CkanAdapter(tf_config()).fetch(make_ctx()).events

    senderismo = events[1]
    assert senderismo.venue_name is None  # source has no place
    assert senderismo.municipality is None
    assert senderismo.island == "Tenerife"  # single configured island
    assert senderismo.organizer_name is None  # "1938.0" is an id, not a name
    assert events[3].venue_name is None  # "Por determinar"


@respx.mock
def test_package_metadata_is_not_stale_when_recent(make_ctx: MakeCtx) -> None:
    mock_tenerife()

    assert CkanAdapter(tf_config()).fetch(make_ctx()).warnings == []


@respx.mock
def test_stale_dataset_warns(make_ctx: MakeCtx) -> None:
    mock_tenerife()
    late = datetime(2027, 6, 1, tzinfo=TZ)

    result = CkanAdapter(tf_config()).fetch(make_ctx(now=late))

    assert any("stale" in w and "2026-10-10" in w for w in result.warnings)


@respx.mock
def test_custom_field_map_and_records_key(make_ctx: MakeCtx) -> None:
    events = {
        "items": [{"nombre": "Feria", "inicio": "2026-10-21 10:00:00", "web": "https://e.org/f"}]
    }
    mock_tenerife(events=events)
    config = tf_config(
        params={
            "records_key": "items",
            "field_map": {"title": "nombre", "start": "inicio", "url": "web"},
        }
    )

    result = CkanAdapter(config).fetch(make_ctx())

    assert [e.title for e in result.events] == ["Feria"]
    assert result.events[0].official_event_url == "https://e.org/f"


@respx.mock
def test_top_level_list_records(make_ctx: MakeCtx) -> None:
    mock_tenerife(events=[{"evento_nombre": "A", "evento_fecha_inicio": "2026-10-21 10:00:00"}])

    assert len(CkanAdapter(tf_config()).fetch(make_ctx()).events) == 1


@respx.mock
def test_incomplete_records_are_discarded(make_ctx: MakeCtx) -> None:
    events = fixture_json("tenerife_sports_events.json")
    events["eventos"] += [
        {
            "evento_nombre": "",
            "evento_url": "https://x.org/1",
            "evento_fecha_inicio": "2026-10-21 10:00:00",
        },
        {
            "evento_nombre": "Sin fecha",
            "evento_url": "https://x.org/2",
            "evento_fecha_inicio": None,
        },
        {
            "evento_nombre": "Fecha rota",
            "evento_url": "https://x.org/3",
            "evento_fecha_inicio": "mañana",
        },
    ]
    mock_tenerife(events=events)

    result = CkanAdapter(tf_config()).fetch(make_ctx())

    assert result.records_received == 10 and len(result.events) == 7
    assert result.discarded == [
        ("missing title", "https://x.org/1"),
        ("missing start date", "Sin fecha"),
        ("missing start date", "Fecha rota"),
    ]


# --------------------------------------------------------------------------- CSV (Gobcan)


@respx.mock
def test_maps_real_gobcan_csv(make_ctx: MakeCtx) -> None:
    mock_gobcan()

    result = CkanAdapter(gc_config()).fetch(make_ctx())

    assert result.records_received == 5 and len(result.events) == 5
    tea = result.events[0]
    assert tea.title == "'Gravedad y órbita. Colecciones TEA'"
    assert tea.all_day is True and tea.start_datetime is None
    assert tea.start_date == date(2021, 4, 16) and tea.end_date == date(2022, 3, 17)
    assert tea.category == "Exposiciones"
    assert tea.island == "Tenerife" and tea.municipality == "Santa Cruz de Tenerife"
    assert tea.venue_name == "TEA Tenerife Espacio de las Artes"
    assert tea.address is None
    assert tea.official_event_url == "https://bit.ly/3g9XOEO"
    assert tea.image_url and tea.image_url.endswith("exposicion.jpg")
    moya = result.events[1]
    assert moya.island == "Gran Canaria" and moya.municipality == "Moya"
    laguna = result.events[2]
    assert laguna.venue_name is None  # "---" placeholder
    assert laguna.address == "Centro de Documentación de Canarias y América (Cedocam)"
    assert laguna.municipality == "San Cristóbal de La Laguna"


@respx.mock
def test_csv_dataset_is_reported_stale(make_ctx: MakeCtx) -> None:
    mock_gobcan()

    result = CkanAdapter(gc_config()).fetch(make_ctx())

    assert any("stale" in w and "2025-03-06" in w for w in result.warnings)


@respx.mock
def test_csv_with_hour_and_minute_builds_datetime(make_ctx: MakeCtx) -> None:
    csv_text = (
        "FECHA_INICIO,FECHA_FIN,DURACION_DIAS,TIPO_EVENTO,ISLA,MUNICIPIO,DENOMINACION_ESPACIO,"
        "LUGAR,TITULO,DESCRIPCION,HORA,MINUTO,MAS_INFO,URL_IMAGEN\n"
        "2026-10-21,2026-10-21,1,Teatro,La Palma,Mazo,Teatro Circo,,Obra,Desc,19,30,"
        "https://e.org/x,\n"
    )
    mock_gobcan(csv_text.encode())

    event = CkanAdapter(gc_config()).fetch(make_ctx()).events[0]

    assert event.start_datetime == datetime(2026, 10, 21, 19, 30, tzinfo=TZ)
    assert event.all_day is False
    assert event.end_date == date(2026, 10, 21)
    assert event.island == "La Palma" and event.municipality == "Villa de Mazo"


@respx.mock
def test_csv_semicolon_delimiter_and_latin1(make_ctx: MakeCtx) -> None:
    text = fixture_bytes("gobcan_agenda_proxima.csv").decode("utf-8").replace(",", ";")
    mock_gobcan(text.encode("latin-1", errors="replace"))

    result = CkanAdapter(gc_config()).fetch(make_ctx())

    assert len(result.events) >= 1  # delimiter is sniffed, encoding falls back to latin-1


# --------------------------------------------------------------------------- package errors


@respx.mock
def test_resource_selected_by_id_and_format(make_ctx: MakeCtx) -> None:
    package = fixture_json("ckan_package_show_tenerife_sports.json")
    dictionary_url = package["result"]["resources"][2]["url"]
    dictionary = respx.get(dictionary_url).mock(return_value=httpx.Response(200, json={}))
    mock_tenerife(package=package)

    CkanAdapter(tf_config()).fetch(make_ctx())

    assert dictionary.call_count == 0


@respx.mock
def test_no_matching_resource_is_invalid(make_ctx: MakeCtx) -> None:
    mock_tenerife()

    with pytest.raises(InvalidPayload, match="no XML resource"):
        CkanAdapter(tf_config(params={"resource_format": "XML"})).fetch(make_ctx())


@respx.mock
def test_unsuccessful_package_show_is_invalid(make_ctx: MakeCtx) -> None:
    respx.get(TF_URL).mock(return_value=httpx.Response(200, json={"success": False}))

    with pytest.raises(InvalidPayload):
        CkanAdapter(tf_config()).fetch(make_ctx())


@respx.mock
def test_resource_on_foreign_host_is_refused(make_ctx: MakeCtx) -> None:
    package = fixture_json("ckan_package_show_tenerife_sports.json")
    package["result"]["resources"][1]["url"] = "https://evil.example.net/data.json"
    respx.get(TF_URL).mock(return_value=httpx.Response(200, json=package))
    evil = respx.get(host="evil.example.net").mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(UnsafeUrlError):
        CkanAdapter(tf_config()).fetch(make_ctx())
    assert evil.call_count == 0


@respx.mock
def test_http_error_propagates(make_ctx: MakeCtx) -> None:
    respx.get(TF_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(HttpError):
        CkanAdapter(tf_config()).fetch(make_ctx())
