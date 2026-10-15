from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from canarias_events.discovery import DEFAULT_TERMS, run_discovery
from canarias_events.http import SafeHttpClient
from tests.conftest import NOW

CKAN_HOSTS = {
    "datos.canarias.es": "/catalogos/general/api/3/action/package_search",
    "datos.tenerife.es": "/ckan/api/3/action/package_search",
    "www.santacruzdetenerife.es": "/opendata/api/3/action/package_search",
}


@pytest.fixture
def client() -> Iterator[SafeHttpClient]:
    http = SafeHttpClient(
        resolver=lambda _h: ["93.184.216.34"], min_interval=0, retries=0, sleep=lambda _s: None
    )
    yield http
    http.close()


def ckan_payload(count: int) -> dict[str, object]:
    return {
        "success": True,
        "result": {
            "count": count,
            "results": [{"name": f"ds-{i}", "title": f"Agenda {i}"} for i in range(min(count, 3))],
        },
    }


def mock_ckan(counts: dict[str, int] | None = None) -> dict[str, respx.Route]:
    routes = {}
    for host, path in CKAN_HOSTS.items():
        count = (counts or {}).get(host, 0)
        routes[host] = respx.get(host=host, path=path).mock(
            return_value=httpx.Response(200, json=ckan_payload(count))
        )
    return routes


def mock_gob(items: list[dict[str, object]] | None = None) -> respx.Route:
    return respx.get(
        host="datos.gob.es", path__regex=r"/apidata/catalog/dataset/title/.*\.json"
    ).mock(return_value=httpx.Response(200, json={"result": {"items": items or []}}))


def results_of(report: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    results = report["results"]
    assert isinstance(results, list)
    return [r for r in results if r["kind"] == kind]


@respx.mock
def test_ckan_search_queries_every_portal_and_term(client: SafeHttpClient) -> None:
    routes = mock_ckan({"datos.tenerife.es": 4})
    mock_gob()
    report = run_discovery(client, probes=[], now=NOW)

    assert DEFAULT_TERMS == ("agenda", "eventos", "actividades", "fiestas")
    for route in routes.values():
        assert route.call_count == 4
    first = routes["datos.tenerife.es"].calls[0].request.url.params
    assert first["rows"] == "50"
    ckan = results_of(report, "ckan_search")
    assert len(ckan) == 12
    tenerife = [r for r in ckan if "datos.tenerife.es" in str(r["endpoint"])]
    assert all(r["items_found"] == 4 and r["needs_manual_check"] for r in tenerife)
    assert tenerife[0]["status"] == 200
    assert tenerife[0]["datasets"][0] == {"name": "ds-0", "title": "Agenda 0"}
    empty = [r for r in ckan if "datos.canarias.es" in str(r["endpoint"])]
    assert all(r["items_found"] == 0 and not r["needs_manual_check"] for r in empty)


@respx.mock
def test_datos_gob_es_keeps_only_canarian_datasets(client: SafeHttpClient) -> None:
    mock_ckan()
    route = mock_gob(
        [
            {
                "_about": "https://datos.gob.es/catalogo/a",
                "title": [{"_value": "Agenda de Tenerife"}],
            },
            {"_about": "https://datos.gob.es/catalogo/b", "title": [{"_value": "Eventos Madrid"}]},
            {
                "_about": "https://datos.gob.es/catalogo/c",
                "title": "Fiestas",
                "description": [{"_value": "Municipios de Canarias"}],
            },
        ]
    )
    report = run_discovery(client, probes=[], now=NOW)
    gob = results_of(report, "datos_gob_es")
    assert len(gob) == 4
    assert route.calls[0].request.url.path == "/apidata/catalog/dataset/title/agenda.json"
    assert gob[0]["items_found"] == 2
    assert gob[0]["needs_manual_check"] is True
    assert [d["url"] for d in gob[0]["datasets"]] == [
        "https://datos.gob.es/catalogo/a",
        "https://datos.gob.es/catalogo/c",
    ]


@respx.mock
def test_domain_probes_classify_tribe_ical_and_jsonapi(client: SafeHttpClient) -> None:
    mock_ckan()
    mock_gob()
    respx.get("https://tribe.test/wp-json/tribe/events/v1/events", params={"per_page": "1"}).mock(
        return_value=httpx.Response(200, json={"events": [{"id": 1}], "total": 12})
    )
    respx.get("https://tribe.test/", params={"ical": "1"}).mock(return_value=httpx.Response(404))
    respx.get("https://tribe.test/jsonapi/node/event").mock(return_value=httpx.Response(404))
    respx.get("https://ical.test/wp-json/tribe/events/v1/events").mock(
        return_value=httpx.Response(200, text="<html>nope</html>")
    )
    respx.get("https://ical.test/", params={"ical": "1"}).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/calendar"},
            text="BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VEVENT\nBEGIN:VEVENT\nEND:VEVENT\nEND:VCALENDAR",
        )
    )
    respx.get("https://ical.test/jsonapi/node/event").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "x"}]})
    )
    respx.route(host="blocked.test").mock(return_value=httpx.Response(403))

    report = run_discovery(
        client, probes=["tribe.test", "https://ical.test", "blocked.test"], now=NOW
    )

    by = {(r["domain"], r["kind"]): r for r in report["results"] if "domain" in r}
    tribe = by[("tribe.test", "tribe_events")]
    assert (tribe["status"], tribe["items_found"], tribe["needs_manual_check"]) == (200, 12, True)
    assert by[("tribe.test", "ical")]["status"] == 404
    assert by[("tribe.test", "ical")]["needs_manual_check"] is False
    assert by[("ical.test", "tribe_events")]["items_found"] == 0  # 200 but not Tribe JSON
    assert by[("ical.test", "tribe_events")]["needs_manual_check"] is False
    ical = by[("ical.test", "ical")]
    assert (ical["items_found"], ical["needs_manual_check"]) == (2, True)
    assert by[("ical.test", "drupal_jsonapi")]["items_found"] == 1
    blocked = by[("blocked.test", "tribe_events")]
    assert blocked["status"] == 403
    assert blocked["needs_manual_check"] is True
    summary = report["summary"]
    assert summary["needs_manual_check"] >= 4


@respx.mock
def test_ical_detected_from_body_when_content_type_is_generic(client: SafeHttpClient) -> None:
    mock_ckan()
    mock_gob()
    respx.get("https://x.test/", params={"ical": "1"}).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/plain"}, text="BEGIN:VCALENDAR\nEND:VCALENDAR"
        )
    )
    respx.route(host="x.test").mock(return_value=httpx.Response(404))
    report = run_discovery(client, probes=["x.test"], now=NOW)
    ical = [r for r in report["results"] if r["kind"] == "ical"][0]
    assert ical["needs_manual_check"] is True
    assert ical["items_found"] == 0


@respx.mock
def test_transport_failures_are_recorded_not_raised(client: SafeHttpClient) -> None:
    respx.route(host="datos.canarias.es").mock(side_effect=httpx.ConnectError("down"))
    respx.route(host="datos.tenerife.es").mock(return_value=httpx.Response(503))
    respx.route(host="www.santacruzdetenerife.es").mock(return_value=httpx.Response(200, text="no"))
    mock_gob()
    report = run_discovery(client, probes=[], now=NOW)
    ckan = results_of(report, "ckan_search")
    assert {r["status"] for r in ckan} == {None, 503, 200}
    assert all(r["needs_manual_check"] is False for r in ckan)
    assert all("error" in r for r in ckan)


@respx.mock
def test_report_metadata(client: SafeHttpClient) -> None:
    mock_ckan()
    mock_gob()
    report = run_discovery(client, probes=[], now=NOW)
    assert report["generated_at"] == NOW.isoformat()
    assert "never enabled automatically" in str(report["notes"])
