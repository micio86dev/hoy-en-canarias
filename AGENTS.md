# Code Review Rules

## Python
- Python >= 3.12, fully type-annotated; `uv run mypy src` must pass (strict).
- `uv run ruff check .` and `uv run pytest` must pass; tests are written first (TDD).
- All code, comments, docs and commit messages are in English; Conventional Commits only.

## Security
- Only http/https; every outbound request goes through `SafeHttpClient` (SSRF guard). The only
  exceptions are the optional browser renderers, which validate every request and redirect hop
  with the same `validate_url`, and the loopback-only Ollama endpoint.
- Never log secrets (API keys, tokens); mask query values of key/token params.
- HTML is read only for configured sources, with deterministic parsers (JSON-LD, microdata,
  embedded JSON, repeated blocks); respect robots.txt and never bypass blocks, challenges,
  authentication or paywalls (a 401/403/challenge page means status `blocked`).
- AI is optional, local only (loopback Ollama), disabled by default and never sees raw HTML;
  no paid APIs.
- Exception (decided by the owner): TypeSafe jev may moderate visitor-submitted event text, a
  narrow yes/no probability, only after contact details are removed, only as one signal next
  to deterministic rules. It never publishes on its own in the uncertain band, and it is off
  unless TYPESAFE_API_KEY is set. Source pages still never reach any model. Optional renderers (Playwright/Crawl4AI) are imported lazily and sandboxed.

## Data
- Never guess missing fields (price, island, dates): use null.
- Timestamps are timezone-aware; the working timezone is Atlantic/Canary.
