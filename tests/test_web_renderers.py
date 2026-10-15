"""Optional JS renderers, exercised with fakes (no browser is ever started in the test suite)."""

from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest

from canarias_events.extract.renderers import (
    Crawl4AIRenderer,
    PlaywrightRenderer,
    RenderError,
    RendererUnavailable,
    make_renderer,
)
from canarias_events.http import DEFAULT_USER_AGENT, UnsafeUrlError

PUBLIC = "93.184.216.34"


def resolver(host: str) -> list[str]:
    return [PUBLIC]


# --------------------------------------------------------------------------- Playwright fakes


class FakeFetched:
    def __init__(self, status: int = 200, location: str | None = None) -> None:
        self.status = status
        self.headers = {"location": location} if location else {}


class FakeRoute:
    """A route whose ``fetch`` follows a scripted redirect map (never the network)."""

    def __init__(self, redirects: dict[str, str] | None = None) -> None:
        self.action: str | None = None
        self.fetched: list[str | None] = []
        self.redirects = redirects or {}
        self.current = ""

    def fetch(self, url: str | None = None, **kwargs: Any) -> FakeFetched:
        assert kwargs.get("max_redirects") == 0  # redirects are never followed implicitly
        self.fetched.append(url)
        self.current = url or self.current
        target = self.redirects.get(self.current)
        return FakeFetched(302, target) if target else FakeFetched(200)

    def fulfill(self, response: FakeFetched) -> None:
        self.action = "fulfill"

    def abort(self) -> None:
        self.action = "abort"


class FakeRequest:
    def __init__(self, url: str, resource_type: str = "document") -> None:
        self.url = url
        self.resource_type = resource_type


def routed(route: FakeRoute, request: FakeRequest) -> FakeRoute:
    route.current = request.url
    return route


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakePage:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.url = ""
        self.closed = False
        self.timeouts: list[int] = []
        self.gotos: list[tuple[str, dict[str, Any]]] = []

    def set_default_timeout(self, ms: int) -> None:
        self.timeouts.append(ms)

    def goto(self, url: str, **kwargs: Any) -> FakeResponse:
        self.gotos.append((url, kwargs))
        self.url = self.context.redirects.get(url, url)
        return FakeResponse(self.context.status)

    def content(self) -> str:
        return self.context.html

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.handlers: list[Any] = []
        self.socket_handlers: list[Any] = []
        self.pages: list[FakePage] = []
        self.html = "<html><body>rendered</body></html>"
        self.status = 200
        self.redirects: dict[str, str] = {}

    def route(self, pattern: str, handler: Any) -> None:
        assert pattern == "**/*"
        self.handlers.append(handler)

    def route_web_socket(self, pattern: str, handler: Any) -> None:
        assert pattern == "**/*"
        self.socket_handlers.append(handler)

    def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        return page

    def close(self) -> None:
        pass


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []
        self.closed = False

    def new_context(self, **kwargs: Any) -> FakeContext:
        ctx = FakeContext(**kwargs)
        self.contexts.append(ctx)
        return ctx

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.browser = FakeBrowser()

    def launch(self, **kwargs: Any) -> FakeBrowser:
        self.launches.append(kwargs)
        return self.browser


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeSyncPlaywright:
    def __init__(self, pw: FakePlaywright) -> None:
        self.pw = pw

    def start(self) -> FakePlaywright:
        return self.pw


def playwright_loader(pw: FakePlaywright) -> Any:
    module = types.SimpleNamespace(sync_playwright=lambda: FakeSyncPlaywright(pw))

    def load(name: str) -> Any:
        assert name == "playwright.sync_api"
        return module

    return load


def _ctx(pw: FakePlaywright) -> FakeContext:
    return pw.chromium.browser.contexts[0]


def test_playwright_missing_gives_a_typed_error() -> None:
    def missing(name: str) -> Any:
        raise ImportError(name)

    renderer = PlaywrightRenderer(loader=missing, resolver=resolver)
    with pytest.raises(RendererUnavailable, match="render"):
        renderer.render("https://example.org/agenda")


def test_the_default_loader_reports_unavailable_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(name: str) -> Any:
        raise ImportError(name)

    monkeypatch.setattr("canarias_events.extract.renderers.importlib.import_module", refuse)
    for renderer in (PlaywrightRenderer(resolver=resolver), Crawl4AIRenderer(resolver=resolver)):
        with pytest.raises(RendererUnavailable):
            renderer.render("https://example.org/agenda")


def test_playwright_single_shared_headless_browser_for_every_url() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    first = renderer.render("https://example.org/a")
    second = renderer.render("https://example.org/b")
    assert first.html == "<html><body>rendered</body></html>"
    assert first.final_url == "https://example.org/a"
    assert first.status == 200
    assert second.final_url == "https://example.org/b"
    assert len(pw.chromium.launches) == 1
    assert pw.chromium.launches[0]["headless"] is True
    assert len(pw.chromium.browser.contexts) == 1
    ctx = _ctx(pw)
    assert ctx.kwargs["user_agent"] == DEFAULT_USER_AGENT  # never impersonates a browser
    assert ctx.kwargs["service_workers"] == "block"
    assert ctx.kwargs["accept_downloads"] is False
    assert all(page.closed for page in ctx.pages)
    renderer.close()
    assert pw.chromium.browser.closed and pw.stopped


def test_playwright_page_timeout_is_applied() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(
        loader=playwright_loader(pw), resolver=resolver, page_timeout_ms=7000
    )
    renderer.render("https://example.org/a")
    page = _ctx(pw).pages[0]
    assert page.timeouts == [7000]
    assert page.gotos[0][1]["timeout"] == 7000


def test_playwright_request_interception_blocks_media_and_private_hosts() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    renderer.render("https://example.org/a")
    handler = _ctx(pw).handlers[0]

    def decide(url: str, kind: str = "document") -> str | None:
        request = FakeRequest(url, kind)
        route = routed(FakeRoute(), request)
        handler(route, request)
        return route.action

    assert decide("https://example.org/app.js", "script") == "fulfill"
    assert decide("https://cdn.example.net/data.json", "xhr") == "fulfill"
    for kind in ("image", "media", "font"):
        assert decide("https://example.org/a.bin", kind) == "abort"
    assert decide("http://127.0.0.1:8080/admin") == "abort"
    assert decide("http://10.0.0.5/") == "abort"
    assert decide("http://169.254.169.254/latest/meta-data") == "abort"
    assert decide("file:///etc/passwd") == "abort"
    assert decide("ftp://example.org/x") == "abort"
    assert decide("javascript:alert(1)") == "abort"
    assert decide("data:text/html,<b>x</b>") == "abort"


def test_playwright_blocks_every_websocket() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    renderer.render("https://example.org/a")
    closed: list[bool] = []

    class FakeSocket:
        url = "ws://10.0.0.5/live"

        def close(self) -> None:
            closed.append(True)

    _ctx(pw).socket_handlers[0](FakeSocket())
    assert closed == [True]


def test_urls_with_credentials_do_not_poison_the_host_verdict() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    renderer.render("https://example.org/a")
    handler = _ctx(pw).handlers[0]
    verdicts = []
    for url in ("https://user:pw@example.org/x", "https://example.org/x"):
        request = FakeRequest(url, "xhr")
        route = routed(FakeRoute(), request)
        handler(route, request)
        verdicts.append(route.action)
    assert verdicts == ["abort", "fulfill"]


def test_redirects_are_validated_hop_by_hop_before_the_browser_follows_them() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    renderer.render("https://example.org/a")
    handler = _ctx(pw).handlers[0]

    def run(start: str, redirects: dict[str, str]) -> FakeRoute:
        request = FakeRequest(start, "xhr")
        route = routed(FakeRoute(redirects), request)
        handler(route, request)
        return route

    # public -> private: refused before the private address is ever requested
    blocked = run(
        "https://example.org/x", {"https://example.org/x": "http://169.254.169.254/latest"}
    )
    assert blocked.action == "abort"
    assert blocked.fetched == [None]
    # public -> public -> public is followed (and finally served)
    chain = {"https://example.org/x": "https://example.org/y", "https://example.org/y": "/z"}
    followed = run("https://example.org/x", chain)
    assert followed.action == "fulfill"
    assert followed.fetched == [None, "https://example.org/y", "https://example.org/z"]
    # public -> public -> private is refused at the second hop
    mixed = {
        "https://example.org/x": "https://example.org/y",
        "https://example.org/y": "http://10.0.0.1/",
    }
    assert run("https://example.org/x", mixed).action == "abort"
    # redirect loops stop
    loop = {"https://example.org/x": "https://example.org/x"}
    assert run("https://example.org/x", loop).action == "abort"
    # a redirect without a usable Location is refused
    assert (
        run("https://example.org/x", {"https://example.org/x": "javascript:alert(1)"}).action
        == "abort"
    )


def test_playwright_interception_rejects_hosts_that_resolve_to_private_ips() -> None:
    pw = FakePlaywright()

    def evil(host: str) -> list[str]:
        return ["10.0.0.9"] if host == "rebind.example.org" else [PUBLIC]

    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=evil)
    renderer.render("https://example.org/a")
    handler = _ctx(pw).handlers[0]
    request = FakeRequest("https://rebind.example.org/x", "xhr")
    route = routed(FakeRoute(), request)
    handler(route, request)
    assert route.action == "abort"
    assert route.fetched == []  # nothing was sent


def test_playwright_refuses_unsafe_start_urls_without_touching_the_browser() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    for url in ("http://127.0.0.1/", "file:///etc/hosts", "ftp://x.org/"):
        with pytest.raises(UnsafeUrlError):
            renderer.render(url)
    assert pw.chromium.launches == []


def test_playwright_rejects_redirects_to_private_hosts() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    renderer.render("https://example.org/warm-up")
    _ctx(pw).redirects["https://example.org/go"] = "http://10.0.0.1/secret"
    with pytest.raises(UnsafeUrlError):
        renderer.render("https://example.org/go")


def test_playwright_http_errors_are_render_errors() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    renderer.render("https://example.org/warm-up")
    _ctx(pw).status = 403
    with pytest.raises(RenderError) as info:
        renderer.render("https://example.org/a")
    assert info.value.status == 403


def test_playwright_html_size_is_capped() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(
        loader=playwright_loader(pw), resolver=resolver, max_html_chars=100
    )
    renderer.render("https://example.org/warm-up")
    _ctx(pw).html = "x" * 500
    with pytest.raises(RenderError, match="too large"):
        renderer.render("https://example.org/a")


def test_render_after_close_starts_nothing_new() -> None:
    pw = FakePlaywright()
    renderer = PlaywrightRenderer(loader=playwright_loader(pw), resolver=resolver)
    renderer.render("https://example.org/a")
    renderer.close()
    renderer.close()  # idempotent
    with pytest.raises(RenderError, match="closed"):
        renderer.render("https://example.org/b")


# --------------------------------------------------------------------------- Crawl4AI fakes


class FakeCrawlResult:
    def __init__(self, **kwargs: Any) -> None:
        self.html = kwargs.get("html", "<html><body>c4a</body></html>")
        self.success = kwargs.get("success", True)
        self.status_code = kwargs.get("status_code", 200)
        self.redirected_url = kwargs.get("redirected_url")
        self.error_message = kwargs.get("error_message")


class FakeStrategy:
    def __init__(self) -> None:
        self.hooks: dict[str, Any] = {}

    def set_hook(self, name: str, hook: Any) -> None:
        self.hooks[name] = hook


class FakeCrawler:
    instances: list[FakeCrawler] = []

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self.crawler_strategy = FakeStrategy()
        self.started = 0
        self.closed = 0
        self.runs: list[tuple[str, Any]] = []
        self.next_result = FakeCrawlResult()
        FakeCrawler.instances.append(self)

    async def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        self.closed += 1

    async def arun(self, url: str, config: Any = None) -> FakeCrawlResult:
        await asyncio.sleep(0)
        self.runs.append((url, config))
        return self.next_result


class Cfg:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def crawl4ai_loader() -> Any:
    FakeCrawler.instances.clear()
    module = types.SimpleNamespace(
        AsyncWebCrawler=FakeCrawler,
        BrowserConfig=Cfg,
        CrawlerRunConfig=Cfg,
        CacheMode=types.SimpleNamespace(BYPASS="bypass"),
    )

    def load(name: str) -> Any:
        assert name == "crawl4ai"
        return module

    return load


def test_crawl4ai_missing_gives_a_typed_error() -> None:
    def missing(name: str) -> Any:
        raise ImportError(name)

    with pytest.raises(RendererUnavailable, match="crawl"):
        Crawl4AIRenderer(loader=missing, resolver=resolver).render("https://example.org/a")


def test_crawl4ai_text_mode_project_user_agent_and_one_shared_crawler() -> None:
    renderer = Crawl4AIRenderer(loader=crawl4ai_loader(), resolver=resolver, page_timeout_ms=9000)
    first = renderer.render("https://example.org/a")
    renderer.render("https://example.org/b")
    assert first.html == "<html><body>c4a</body></html>"
    assert first.final_url == "https://example.org/a"
    crawler = FakeCrawler.instances[0]
    assert len(FakeCrawler.instances) == 1 and crawler.started == 1
    browser_cfg = crawler.config
    assert browser_cfg.headless is True
    assert browser_cfg.text_mode is True
    assert browser_cfg.light_mode is True
    assert browser_cfg.user_agent == DEFAULT_USER_AGENT
    assert browser_cfg.enable_stealth is False
    assert browser_cfg.accept_downloads is False
    run_cfg = crawler.runs[0][1]
    assert run_cfg.cache_mode == "bypass"
    assert run_cfg.page_timeout == 9000
    assert run_cfg.check_robots_txt is False  # robots.txt is enforced by the importer itself
    assert run_cfg.magic is False and run_cfg.simulate_user is False
    renderer.close()
    assert crawler.closed == 1


def test_crawl4ai_registers_a_request_filter_hook() -> None:
    renderer = Crawl4AIRenderer(loader=crawl4ai_loader(), resolver=resolver)
    renderer.render("https://example.org/a")
    hook = FakeCrawler.instances[0].crawler_strategy.hooks["on_page_context_created"]
    routed: list[Any] = []

    class Ctx:
        async def route(self, pattern: str, handler: Any) -> None:
            routed.append((pattern, handler))

    sockets: list[Any] = []

    class SocketCtx(Ctx):
        async def route_web_socket(self, pattern: str, handler: Any) -> None:
            sockets.append(handler)

    asyncio.run(hook(object(), context=SocketCtx(), config=None))
    assert routed and routed[0][0] == "**/*"
    closed: list[bool] = []

    class AsyncSocket:
        async def close(self) -> None:
            closed.append(True)

    asyncio.run(sockets[0](AsyncSocket()))
    assert closed == [True]
    handler = routed[0][1]

    async def decide(
        url: str, kind: str = "xhr", redirects: dict[str, str] | None = None
    ) -> str | None:
        route = FakeAsyncRoute(redirects)
        route.current = url
        await handler(route, FakeRequest(url, kind))
        return route.action

    assert asyncio.run(decide("https://cdn.example.net/a.json")) == "fulfill"
    assert (
        asyncio.run(
            decide("https://example.org/x", redirects={"https://example.org/x": "http://10.0.0.1/"})
        )
        == "abort"
    )
    assert asyncio.run(decide("http://127.0.0.1/x")) == "abort"
    assert asyncio.run(decide("https://example.org/p.png", "image")) == "abort"


class FakeAsyncRoute:
    def __init__(self, redirects: dict[str, str] | None = None) -> None:
        self.action: str | None = None
        self.redirects = redirects or {}
        self.current = ""

    async def fetch(self, url: str | None = None, **kwargs: Any) -> FakeFetched:
        assert kwargs.get("max_redirects") == 0
        self.current = url or self.current
        target = self.redirects.get(self.current)
        return FakeFetched(302, target) if target else FakeFetched(200)

    async def fulfill(self, response: FakeFetched) -> None:
        self.action = "fulfill"

    async def abort(self) -> None:
        self.action = "abort"


def test_crawl4ai_failures_become_render_errors() -> None:
    renderer = Crawl4AIRenderer(loader=crawl4ai_loader(), resolver=resolver)
    renderer.render("https://example.org/warm-up")
    crawler = FakeCrawler.instances[0]
    crawler.next_result = FakeCrawlResult(success=False, status_code=403, error_message="blocked")
    with pytest.raises(RenderError) as info:
        renderer.render("https://example.org/a")
    assert info.value.status == 403
    crawler.next_result = FakeCrawlResult(redirected_url="http://10.1.1.1/x")
    with pytest.raises(UnsafeUrlError):
        renderer.render("https://example.org/a")


def test_crawl4ai_refuses_unsafe_start_urls() -> None:
    renderer = Crawl4AIRenderer(loader=crawl4ai_loader(), resolver=resolver)
    with pytest.raises(UnsafeUrlError):
        renderer.render("http://169.254.169.254/")
    assert FakeCrawler.instances == []


# --------------------------------------------------------------------------- factory


def test_make_renderer_by_name() -> None:
    assert isinstance(make_renderer("playwright"), PlaywrightRenderer)
    assert isinstance(make_renderer("crawl4ai"), Crawl4AIRenderer)
    with pytest.raises(ValueError):
        make_renderer("selenium")
