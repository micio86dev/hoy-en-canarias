# Code Review Rules

## Python
- Python >= 3.12, fully type-annotated; `uv run mypy src` must pass (strict).
- `uv run ruff check .` and `uv run pytest` must pass; tests are written first (TDD).
- All code, comments, docs and commit messages are in English; Conventional Commits only.

## Security
- Only http/https; every outbound request goes through `SafeHttpClient` (SSRF guard).
- Never log secrets (API keys, tokens); mask query values of key/token params.
- No HTML scraping, no AI calls, no paid APIs.

## Data
- Never guess missing fields (price, island, dates): use null.
- Timestamps are timezone-aware; the working timezone is Atlantic/Canary.
