"""Optional JavaScript renderers (Playwright, Crawl4AI), imported lazily.

Both run one headless browser per importer run and reuse it for every URL. Every request the page
makes is fetched by the renderer itself, hop by hop, through the SSRF validator of
:mod:`canarias_events.http`; images, media, fonts and WebSockets are blocked; the browser
identifies itself with the importer's own User-Agent (no impersonation, no stealth mode) and page
scripts only ever run inside the sandboxed browser.

Residual risk (the same as :mod:`canarias_events.http`): the browser resolves hostnames again when
it connects, so a DNS rebinding window remains.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from canarias_events.http import DEFAULT_USER_AGENT, UnsafeUrlError, mask_secrets, validate_url

logger = logging.getLogger("canarias_events.render")

BLOCKED_RESOURCES = frozenset({"image", "media", "font", "imageset"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECT_HOPS = 5
DEFAULT_PAGE_TIMEOUT_MS = 20_000
DEFAULT_MAX_HTML_CHARS = 5_000_000
SETTLE_MS = 3000

Loader = Callable[[str], Any]
Resolver = Callable[[str], list[str]]


class RendererUnavailable(Exception):
    """The optional renderer (or its browser) is not installed or cannot start."""


class RenderError(Exception):
    """The page could not be rendered (HTTP error, timeout, oversized document)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RenderedPage:
    html: str
    final_url: str
    status: int | None = None


class Renderer(Protocol):
    name: str

    def render(self, url: str) -> RenderedPage: ...

    def close(self) -> None: ...


class _RequestFilter:
    """Allow only public http(s) requests (every hostname is resolved and checked once)."""

    def __init__(self, resolver: Resolver | None) -> None:
        self._resolver = resolver
        self._verdicts: dict[str, bool] = {}

    def url_ok(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
            host = parts.hostname
        except ValueError:
            return False
        if parts.scheme.lower() not in ("http", "https") or not host or "@" in parts.netloc:
            return False
        if host not in self._verdicts:
            try:
                validate_url(url, self._resolver)
                self._verdicts[host] = True
            except UnsafeUrlError:
                self._verdicts[host] = False
        return self._verdicts[host]

    def allows(self, url: str, resource_type: str) -> bool:
        return resource_type not in BLOCKED_RESOURCES and self.url_ok(url)

    def redirect_target(self, current: str, headers: Any) -> str | None:
        """Absolute, validated target of a redirect response; None when missing or not allowed."""
        location = headers.get("location") if hasattr(headers, "get") else None
        if not isinstance(location, str) or not location.strip():
            return None
        target = urljoin(current, location.strip())
        return target if self.url_ok(target) else None


def _check_start_url(url: str, resolver: Resolver | None) -> None:
    validate_url(url, resolver)  # raises UnsafeUrlError (scheme, private hosts, credentials)


def _check_status(status: int | None) -> None:
    if status is not None and status >= 400:
        raise RenderError(f"HTTP {status}", status)


# --------------------------------------------------------------------------- Playwright


class PlaywrightRenderer:
    name = "playwright"

    def __init__(
        self,
        *,
        loader: Loader | None = None,
        resolver: Resolver | None = None,
        page_timeout_ms: int = DEFAULT_PAGE_TIMEOUT_MS,
        max_html_chars: int = DEFAULT_MAX_HTML_CHARS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._load = loader or importlib.import_module
        self._resolver = resolver
        self._timeout = page_timeout_ms
        self._max_chars = max_html_chars
        self._user_agent = user_agent
        self._filter = _RequestFilter(resolver)
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._closed = False

    def _start(self) -> Any:
        if self._context is not None:
            return self._context
        try:
            module = self._load("playwright.sync_api")
        except ImportError as exc:
            raise RendererUnavailable(
                "playwright is not installed: pip install 'canarias-events[render]' "
                "&& playwright install chromium"
            ) from exc
        try:
            self._pw = module.sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=self._user_agent,
                java_script_enabled=True,
                accept_downloads=False,
                service_workers="block",
            )
            self._context.route("**/*", self._handle)
            # route() does not see WebSockets: the listing pages never need one, so refuse all.
            self._context.route_web_socket("**/*", self._handle_web_socket)
        except Exception as exc:  # noqa: BLE001 - browser binaries missing, sandbox errors...
            self.close()
            self._closed = False
            raise RendererUnavailable(f"cannot start chromium: {exc}") from exc
        return self._context

    def _handle(self, route: Any, request: Any) -> None:
        """Fetch the request ourselves without following redirects, so every hop is validated.

        Playwright calls route handlers only for the first URL of a redirect chain.
        """
        try:
            if not self._filter.allows(request.url, request.resource_type):
                route.abort()
                return
            current = request.url
            response = route.fetch(max_redirects=0)
            for _ in range(MAX_REDIRECT_HOPS + 1):
                if response.status not in REDIRECT_STATUSES:
                    route.fulfill(response=response)
                    return
                target = self._filter.redirect_target(current, response.headers)
                if target is None:
                    break
                current = target
                response = route.fetch(url=current, max_redirects=0)
            route.abort()
        except Exception:  # noqa: BLE001 - never let a handler bug open the gate
            route.abort()

    @staticmethod
    def _handle_web_socket(web_socket: Any) -> None:
        web_socket.close()

    def render(self, url: str) -> RenderedPage:
        if self._closed:
            raise RenderError("renderer is closed")
        _check_start_url(url, self._resolver)
        context = self._start()
        page = context.new_page()
        try:
            page.set_default_timeout(self._timeout)
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
            except Exception as exc:  # noqa: BLE001 - net errors, navigation timeouts
                raise RenderError(f"navigation failed: {type(exc).__name__}") from exc
            status = getattr(response, "status", None)
            _check_status(status)
            settle = getattr(page, "wait_for_load_state", None)
            if settle is not None:
                try:
                    settle("networkidle", timeout=min(self._timeout, SETTLE_MS))
                except Exception:  # noqa: BLE001 - pages that poll forever never go idle
                    logger.debug("network never went idle for %s", mask_secrets(url))
            html = page.content()
            final_url = str(page.url)
            _check_start_url(final_url, self._resolver)
            if len(html) > self._max_chars:
                raise RenderError("rendered document too large")
            return RenderedPage(html, final_url, status)
        finally:
            page.close()

    def close(self) -> None:
        self._closed = True
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer is not None:
                try:
                    closer()
                except Exception:  # noqa: BLE001 - closing is best effort
                    logger.debug("error while closing the playwright renderer", exc_info=True)
        self._context = self._browser = self._pw = None


# --------------------------------------------------------------------------- Crawl4AI


class Crawl4AIRenderer:
    """Crawl4AI 0.9.x in text mode: ``AsyncWebCrawler.arun`` driven from a private event loop."""

    name = "crawl4ai"

    def __init__(
        self,
        *,
        loader: Loader | None = None,
        resolver: Resolver | None = None,
        page_timeout_ms: int = DEFAULT_PAGE_TIMEOUT_MS,
        max_html_chars: int = DEFAULT_MAX_HTML_CHARS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._load = loader or importlib.import_module
        self._resolver = resolver
        self._timeout = page_timeout_ms
        self._max_chars = max_html_chars
        self._user_agent = user_agent
        self._filter = _RequestFilter(resolver)
        self._module: Any = None
        self._crawler: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    def _run(self, coro: Any) -> Any:
        assert self._loop is not None
        return self._loop.run_until_complete(coro)

    def _start(self) -> Any:
        if self._crawler is not None:
            return self._crawler
        try:
            self._module = self._load("crawl4ai")
        except ImportError as exc:
            raise RendererUnavailable(
                "crawl4ai is not installed: pip install 'canarias-events[crawl]' && crawl4ai-setup"
            ) from exc
        module = self._module
        self._loop = asyncio.new_event_loop()
        try:
            browser_config = module.BrowserConfig(
                headless=True,
                text_mode=True,
                light_mode=True,
                verbose=False,
                user_agent=self._user_agent,
                enable_stealth=False,
                accept_downloads=False,
                java_script_enabled=True,
            )
            crawler = module.AsyncWebCrawler(config=browser_config)
            crawler.crawler_strategy.set_hook("on_page_context_created", self._on_context)
            self._run(crawler.start())
        except Exception as exc:  # noqa: BLE001 - browser binaries missing, sandbox errors...
            self._loop.close()
            self._loop = None
            raise RendererUnavailable(f"cannot start crawl4ai: {exc}") from exc
        self._crawler = crawler
        return crawler

    async def _on_context(self, page: Any, context: Any = None, **_: Any) -> Any:
        await context.route("**/*", self._handle)
        await context.route_web_socket("**/*", self._handle_web_socket)
        return page

    @staticmethod
    async def _handle_web_socket(web_socket: Any) -> None:
        await web_socket.close()

    async def _handle(self, route: Any, request: Any) -> None:
        """Same hop-by-hop validation as the Playwright renderer, on the async API."""
        try:
            if not self._filter.allows(request.url, request.resource_type):
                await route.abort()
                return
            current = request.url
            response = await route.fetch(max_redirects=0)
            for _ in range(MAX_REDIRECT_HOPS + 1):
                if response.status not in REDIRECT_STATUSES:
                    await route.fulfill(response=response)
                    return
                target = self._filter.redirect_target(current, response.headers)
                if target is None:
                    break
                current = target
                response = await route.fetch(url=current, max_redirects=0)
            await route.abort()
        except Exception:  # noqa: BLE001 - never let a handler bug open the gate
            await route.abort()

    def render(self, url: str) -> RenderedPage:
        if self._closed:
            raise RenderError("renderer is closed")
        _check_start_url(url, self._resolver)
        crawler = self._start()
        module = self._module
        run_config = module.CrawlerRunConfig(
            cache_mode=module.CacheMode.BYPASS,
            page_timeout=self._timeout,
            wait_until="domcontentloaded",
            delay_before_return_html=0.5,
            check_robots_txt=False,  # robots.txt is enforced by the importer before rendering
            magic=False,
            simulate_user=False,
            override_navigator=False,
            verbose=False,
        )
        try:
            result = self._run(crawler.arun(url=url, config=run_config))
        except Exception as exc:  # noqa: BLE001
            raise RenderError(f"crawl failed: {type(exc).__name__}") from exc
        status = getattr(result, "status_code", None)
        if not getattr(result, "success", False):
            raise RenderError("crawl failed", status)  # crawl4ai messages may echo the URL
        _check_status(status)
        html = str(getattr(result, "html", "") or "")
        final_url = str(getattr(result, "redirected_url", None) or url)
        _check_start_url(final_url, self._resolver)
        if len(html) > self._max_chars:
            raise RenderError("rendered document too large")
        return RenderedPage(html, final_url, status)

    def close(self) -> None:
        self._closed = True
        if self._crawler is not None and self._loop is not None:
            try:
                self._run(self._crawler.close())
            except Exception:  # noqa: BLE001 - closing is best effort
                logger.debug("error while closing crawl4ai", exc_info=True)
        if self._loop is not None:
            self._loop.close()
        self._crawler = None
        self._loop = None


def make_renderer(kind: str, **kwargs: Any) -> Renderer:
    """Renderer by configuration name (``playwright`` or ``crawl4ai``)."""
    if kind == "playwright":
        return PlaywrightRenderer(**kwargs)
    if kind == "crawl4ai":
        return Crawl4AIRenderer(**kwargs)
    raise ValueError(f"unknown renderer: {kind}")
