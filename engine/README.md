# Repcut engine

FastAPI service that owns ingest, analysis, planning, rendering and job state
for Repcut. Local-first: SQLite plus asyncio workers, no external services.

## Layout

```text
repcut/
  config.py    pydantic-settings Settings, sourced from the repo-root .env
  logging.py   structlog JSON logging
  probes.py    synchronous ffmpeg / torch capability probes (run in a thread)
  models.py    Pydantic response models
  main.py      FastAPI app and routes
tests/         CPU-only pytest suite
```

## Development

From the repository root:

```bash
pip install -e "engine[dev]"
ruff check engine && ruff format --check engine
mypy --config-file engine/pyproject.toml engine
pytest engine -m "not gpu" -q
uvicorn repcut.main:app --reload --port 8000
```

Configuration comes from the repo-root `.env` (see `.env.example` for the key
names). Nothing in this package reads a hardcoded path, and no secret value is
ever logged or returned by a route - `/health` reports only
`gemini_api_key_set: true|false`.
