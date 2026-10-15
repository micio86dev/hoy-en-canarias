"""Command line interface: ``python -m canarias_events.cli`` or ``canarias-events``."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv

from canarias_events.crawl_settings import CrawlSettings
from canarias_events.database import Database, SqliteHttpCache, default_db_path
from canarias_events.date_range import TZ, DateWindow, explicit_window, next_week
from canarias_events.discovery import run_discovery
from canarias_events.exporters import export_all, source_status, write_atomic
from canarias_events.health import summarize
from canarias_events.http import FetchError, SafeHttpClient
from canarias_events.logging_config import configure_logging
from canarias_events.manual_import import (
    IMPORT_URL_SOURCE,
    ORGANIZER_SOURCE,
    RefusedUrl,
    hold_for_review,
    import_url,
    publish_now,
)
from canarias_events.models import Event
from canarias_events.pipeline import accept_quarantine, run_import
from canarias_events.social import load_social_registry, schedule_blocker, scheduled_sources
from canarias_events.sources.base import SourceConfig
from canarias_events.sources.registry import SourcesCatalog, load_catalog
from canarias_events.submissions import SubmissionError, load_submission

DEFAULT_CONFIG = Path("config/sources.yaml")
DEFAULT_OUTPUT_DIR = Path("output")

app = typer.Typer(
    help="Import public Canary Islands events for the next Monday-Sunday week.",
    no_args_is_help=True,
    add_completion=False,
)

ConfigOption = Annotated[Path, typer.Option("--config", help="Path to sources.yaml.")]
DbOption = Annotated[
    Path | None, typer.Option("--db", help="SQLite file (default: $CANARIAS_DB_PATH or data/).")
]
SocialConfigOption = Annotated[
    Path | None,
    typer.Option("--social-config", help="Social registry (default: social_sources.yaml)."),
]
OutputOption = Annotated[Path, typer.Option("--output-dir", help="Directory for the outputs.")]


def make_client(cache: SqliteHttpCache | None = None, *, discovery: bool = False) -> SafeHttpClient:
    """HTTP client used by the commands (replaced in tests)."""
    if discovery:
        return SafeHttpClient(timeout=10.0, retries=1, max_bytes=2 * 1024 * 1024)
    return SafeHttpClient(cache=cache)


@app.callback()
def main(
    log_level: Annotated[
        str | None,
        typer.Option("--log-level", envvar="CANARIAS_LOG_LEVEL", help="DEBUG, INFO, WARNING..."),
    ] = None,
) -> None:
    load_dotenv()
    try:
        configure_logging(log_level)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--log-level") from exc


# --------------------------------------------------------------------------- helpers


def _load_catalog(path: Path) -> SourcesCatalog:
    try:
        return load_catalog(path)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"cannot load {path}: {exc}", param_hint="--config") from exc


def _social_path(config: Path, social_config: Path | None) -> Path:
    return social_config or config.parent / "social_sources.yaml"


def _with_social(catalog: SourcesCatalog, path: Path) -> SourcesCatalog:
    """The catalog plus the verified, enabled social accounts (never anything else)."""
    try:
        extra = scheduled_sources(load_social_registry(path))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            f"cannot load {path}: {exc}", param_hint="--social-config"
        ) from exc
    taken = {s.id for s in catalog.sources}
    clash = [s.id for s in extra if s.id in taken]
    if clash:
        raise typer.BadParameter(f"social source id(s) also in sources.yaml: {', '.join(clash)}")
    return catalog.model_copy(update={"sources": [*catalog.sources, *extra]})


def _parse_day(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"{value!r} is not a YYYY-MM-DD date", param_hint=option) from None


def _window(from_date: str | None, to_date: str | None) -> DateWindow:
    if from_date is None and to_date is None:
        return next_week()
    if from_date is None or to_date is None:
        raise typer.BadParameter("--from and --to must be given together")
    start, end = _parse_day(from_date, "--from"), _parse_day(to_date, "--to")
    if end < start:
        raise typer.BadParameter("--to must not be before --from")
    return explicit_window(start, end)


def format_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    cells = [[str(c) for c in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()]
    lines += ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in cells]
    return "\n".join(lines)


def _echo_report(report: dict[str, Any]) -> None:
    window = report["window"]
    typer.echo(
        f"Run {report['run_id']} at {report['executed_at']}: "
        f"window {window['start_date']} to {window['end_date_inclusive']} (Atlantic/Canary)"
    )
    rows = [
        [
            source_id,
            s["status"],
            s["records_received"],
            s["valid_events"],
            s["in_window"],
            s["new"],
            s["updated"],
            s["merged"],
            s["possible_duplicates"],
            f"{s['duration_s']:.1f}s",
        ]
        for source_id, s in report["sources"].items()
    ]
    if rows:
        headers = ["source", "status", "received", "valid", "in_window", "new", "updated",
                   "merged", "dups?", "time"]  # fmt: skip
        typer.echo(format_table(headers, rows))
    for skipped in report["sources_skipped"]:
        typer.echo(f"skipped {skipped['id']}: {skipped['reason']}")
    for error in report["errors"]:
        typer.echo(f"error {error['source']}: {error['error']}")
    for warning in report["warnings"]:
        typer.echo(f"warning {warning['source']}: {warning['warning']}")
    timings = report.get("timings")
    if timings:
        tiers = ", ".join(f"{t} {sec:.1f}s" for t, sec in timings["tiers"].items())
        typer.echo(
            f"Timing: total {timings['total_s']:.1f}s" + (f"; tiers: {tiers}" if tiers else "")
        )
    totals = report["totals"]
    typer.echo(
        f"Events in window: {totals['events_in_window']} "
        f"(new {totals['new']}, updated {totals['updated']}, merged {totals['merged']}, "
        f"possible duplicates {totals['possible_duplicates']})"
    )


def _echo_health(db: Database, history_runs: int) -> None:
    rows: list[list[Any]] = []
    for source_id, row in sorted(db.source_rows().items()):
        h = summarize(db.recent_source_runs(source_id, history_runs))
        if not h["runs_considered"]:
            continue
        rate = h["error_rate"]
        rows.append(
            [
                source_id,
                h["runs_considered"],
                h["last_status"],
                row["last_success_at"] or "-",
                "-" if h["last_events_found"] is None else h["last_events_found"],
                f"{rate:.0%}",
                h["consecutive_failures"],
                ",".join(h["last_anomalies"]) or "-",
            ]
        )
    if not rows:
        typer.echo("No source runs recorded yet.")
        return
    headers = [
        "source",
        "runs",
        "last status",
        "last success",
        "events",
        "errors",
        "streak",
        "anomalies",
    ]
    typer.echo(format_table(headers, rows))


# --------------------------------------------------------------------------- commands


@app.command("import-events")
def import_events(
    from_date: Annotated[str | None, typer.Option("--from", help="First day, YYYY-MM-DD.")] = None,
    to_date: Annotated[str | None, typer.Option("--to", help="Last day (inclusive).")] = None,
    island: Annotated[str | None, typer.Option(help="Only events of this island.")] = None,
    source: Annotated[
        list[str] | None, typer.Option("--source", help="Only this source id (repeatable).")
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Fetch and report only: no database writes or exports."),
    ] = False,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="Never use the local model, whatever the config says.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Ignore source frequency, backoff and cool-downs.")
    ] = False,
    low_resource: Annotated[
        bool,
        typer.Option(
            "--low-resource", help="One source at a time, no renderers or model, fewer pages."
        ),
    ] = False,
    db_path: DbOption = None,
    output_dir: OutputOption = DEFAULT_OUTPUT_DIR,
    config: ConfigOption = DEFAULT_CONFIG,
    social_config: SocialConfigOption = None,
) -> None:
    """Fetch the enabled sources, deduplicate, store and export the window's events."""
    window = _window(from_date, to_date)
    catalog = _with_social(_load_catalog(config), _social_path(config, social_config))
    typer.echo(
        f"Window: {window.start_date.isoformat()} to {window.end_date_inclusive.isoformat()} "
        "(Atlantic/Canary)"
    )
    now = datetime.now(TZ)
    path = db_path or default_db_path()
    ai = catalog.ai.model_copy(update={"enabled": False}) if no_ai else catalog.ai
    snapshots = None if dry_run else output_dir / "snapshots"
    # A dry run works on an in-memory copy: honest counts, and nothing is written to disk.
    with Database.memory_copy(path) if dry_run else Database(path) as db:
        client = make_client(None if dry_run else SqliteHttpCache(db))
        try:
            report = run_import(
                db, catalog.sources, window, client=client, now=now, island=island,
                source_ids=source or None, ai=ai, crawl=catalog.crawl, force=force,
                low_resource=low_resource, snapshot_dir=snapshots,
            )  # fmt: skip
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        finally:
            client.close()
        if dry_run:
            report["dry_run"] = True
            write_atomic(
                output_dir / "dry-run-report.json",
                (json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n").encode(),
            )
        else:
            export_all(
                db, catalog, window, report, output_dir, now=now, island=report["params"]["island"]
            )
    _echo_report(report)
    if dry_run:
        typer.echo(f"Dry run: nothing stored or exported. Report: {output_dir}/dry-run-report.json")
    else:
        typer.echo(f"Outputs written to {output_dir}/")
    if not report["sources_succeeded"]:
        if not report["sources_attempted"] and report["sources_deferred"]:
            # Nothing failed: every selected source was simply not due yet.
            typer.echo(f"No source due; {len(report['sources_skipped'])} skipped.")
            return
        typer.echo("No source succeeded.", err=True)
        raise typer.Exit(1)


def _echo_social(path: Path) -> None:
    try:
        registry = load_social_registry(path)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            f"cannot load {path}: {exc}", param_hint="--social-config"
        ) from exc
    if not registry.sources:
        typer.echo(f"No social sources in {path}.")
        return
    rows = []
    for s in registry.sources:
        blocker = schedule_blocker(s)
        rows.append(
            [s.id, s.platform, s.kind, s.acquisition_mode, s.verification_state,
             ",".join(s.islands) or "-", "yes" if blocker is None else f"no ({blocker})"]
        )  # fmt: skip
    headers = ["id", "platform", "kind", "mode", "state", "islands", "scheduled"]
    typer.echo(format_table(headers, rows))


@app.command("list-sources")
def list_sources(
    social: Annotated[
        bool, typer.Option("--social", help="List the social source registry instead.")
    ] = False,
    config: ConfigOption = DEFAULT_CONFIG,
    social_config: SocialConfigOption = None,
) -> None:
    """List the configured sources and their status."""
    if social:
        _echo_social(_social_path(config, social_config))
        return
    catalog = _load_catalog(config)
    rows = [
        [
            s.id,
            s.adapter,
            "yes" if s.enabled else "no",
            ",".join(s.islands) or "-",
            s.license or "-",
            source_status(s, os.environ),
        ]
        for s in catalog.sources
    ]
    typer.echo(format_table(["id", "adapter", "enabled", "islands", "license", "status"], rows))


@app.command("discover-sources")
def discover_sources(
    output_dir: OutputOption = DEFAULT_OUTPUT_DIR,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Search open-data catalogs and probe candidate domains. Never enables anything."""
    catalog = _load_catalog(config)
    with make_client(discovery=True) as client:
        report = run_discovery(client, probes=catalog.discovery_probes, now=datetime.now(TZ))
    path = output_dir / "discovery-report.json"
    write_atomic(path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    summary = report["summary"]
    typer.echo(
        f"{summary['requests']} requests, {summary['errors']} errors, "
        f"{summary['needs_manual_check']} need a manual check. Report: {path}"
    )
    for result in report["results"]:
        if result["needs_manual_check"]:
            typer.echo(f"  manual check: {result['kind']} {result['endpoint']} -> "
                       f"{result['items_found']} item(s)")  # fmt: skip


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _store_manual(
    events: list[Event], source: SourceConfig, db_path: Path | None, *, dry_run: bool, accept: bool
) -> None:
    """Show, hold for review (default) or publish the events of a one-off import."""
    if dry_run:
        shown = [e.model_dump(mode="json", exclude={"raw_data"}) for e in events]
        typer.echo(json.dumps(shown, ensure_ascii=False, indent=2))
        typer.echo("Dry run: nothing stored.", err=True)
        return
    now = datetime.now(TZ)
    with Database(db_path or default_db_path()) as db:
        if accept:
            stats = publish_now(db, source, events, now)
            typer.echo(
                f"Published {len(events)} event(s): new {stats['new']}, "
                f"updated {stats['updated']}, merged {stats['merged']}, "
                f"unchanged {stats['unchanged']}. "
                "Run import-events to re-export."
            )
            return
        held = hold_for_review(db, source, events, now)
    typer.echo(
        f"Held {held} event(s) for review ({len(events) - held} already waiting). "
        f"See them with `review-quarantine --list`, publish with "
        f"`review-quarantine --accept {source.id}`."
    )


@app.command("import-url")
def import_url_command(
    url: Annotated[str, typer.Argument(help="Public event page (http/https).")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the extracted events; store nothing.")
    ] = False,
    accept: Annotated[
        bool, typer.Option("--accept", help="Publish at once instead of holding for review.")
    ] = False,
    db_path: DbOption = None,
) -> None:
    """Import one public event page given by a person. Held for review unless --accept."""
    client = make_client()
    try:
        outcome = import_url(client, url, now=datetime.now(TZ))
    except RefusedUrl as exc:
        raise _fail(str(exc), 2) from exc
    except ValueError as exc:
        raise _fail(str(exc), 2) from exc
    except FetchError as exc:
        raise _fail(f"cannot read {url}: {exc}", 1) from exc
    finally:
        client.close()
    for warning in outcome.warnings:
        typer.echo(f"warning: {warning}", err=True)
    if outcome.status != "ok" or not outcome.events:
        reason = outcome.status if outcome.status != "ok" else "no_events_found"
        raise _fail(f"Nothing imported ({reason}).", 1)
    _store_manual(outcome.events, IMPORT_URL_SOURCE, db_path, dry_run=dry_run, accept=accept)


@app.command("submit-event")
def submit_event(
    file: Annotated[Path, typer.Option("--file", help="Organizer file: .json or .ics.")],
    organizer: Annotated[
        str | None, typer.Option("--organizer", help="Organizer name (required for .ics).")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the validated events; store nothing.")
    ] = False,
    accept: Annotated[
        bool, typer.Option("--accept", help="Publish at once instead of holding for review.")
    ] = False,
    db_path: DbOption = None,
) -> None:
    """Store an event an organizer chose to send us. Held for review unless --accept."""
    try:
        events = load_submission(file, organizer, datetime.now(TZ))
    except SubmissionError as exc:
        for error in exc.errors:
            typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from exc
    _store_manual(events, ORGANIZER_SOURCE, db_path, dry_run=dry_run, accept=accept)


@app.command("report")
def show_report(
    db_path: DbOption = None,
    run_id: Annotated[int | None, typer.Option("--run-id", help="Default: the latest run.")] = None,
    health: Annotated[
        bool, typer.Option("--health", help="Per-source health over the recent runs.")
    ] = False,
    config: ConfigOption = DEFAULT_CONFIG,
) -> None:
    """Print the summary of an import run stored in the database."""
    with Database(db_path or default_db_path()) as db:
        if health:
            runs = _load_catalog(config).crawl.history_runs if config.exists() else None
            _echo_health(db, runs or CrawlSettings().history_runs)
            return
        run = db.get_run(run_id)
    if run is None or run.report is None:
        typer.echo("No import runs found." if run is None else f"Run {run.id} has no report.")
        raise typer.Exit(1)
    _echo_report(run.report)


@app.command("review-quarantine")
def review_quarantine(
    list_items: Annotated[bool, typer.Option("--list", help="Show what is held back.")] = False,
    accept: Annotated[
        str | None,
        typer.Option(
            "--accept",
            help="Publish a source's held-back events and make that count its baseline.",
        ),
    ] = None,
    db_path: DbOption = None,
) -> None:
    """List or accept events held back because their run looked anomalous."""
    if not list_items and accept is None:
        raise typer.BadParameter("give --list or --accept <source>")
    with Database(db_path or default_db_path()) as db:
        if accept is not None:
            count = accept_quarantine(db, accept, now=datetime.now(TZ))
            if count == 0:
                typer.echo(f"Nothing quarantined for {accept}.", err=True)
                raise typer.Exit(1)
            typer.echo(f"Accepted {count} event(s) of {accept}; run import-events to re-export.")
            return
        pending = db.quarantine_pending()
    if not pending:
        typer.echo("No quarantined events.")
        return
    groups: dict[str, list[Any]] = {}
    for item in pending:
        groups.setdefault(item.source_id, []).append(item)
    rows = [
        [sid, len(items), items[0].reason, items[0].run_id, items[0].created_at[:16],
         "; ".join(i.event.title for i in items[:3])]
        for sid, items in groups.items()
    ]  # fmt: skip
    typer.echo(format_table(["source", "events", "reason", "run", "held at", "first titles"], rows))


if __name__ == "__main__":
    app()
