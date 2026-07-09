# Purchase Agent

Backend API skeleton for Purchase Agent.

This repository currently contains only the minimal FastAPI foundation: app
startup, configuration defaults, a health endpoint, tests, and Python tooling.

## Requirements

- Python 3.12+
- uv

## Install dependencies

```bash
uv sync --dev
```

## Run the API

```bash
uv run uvicorn app.main:app --reload
```

## Run tests

```bash
uv run pytest
```

## Run linting

```bash
uv run ruff check .
```

## Run type checks

```bash
uv run mypy
```
