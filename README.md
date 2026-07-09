# Purchase Agent

Backend API skeleton for Purchase Agent.

This repository currently contains only the minimal FastAPI foundation: app
startup, configuration defaults, a health endpoint, tests, and Python tooling.

## Domain models

The backend includes initial Pydantic domain models for:

- Identity
- Mission
- ProviderOption
- ExecutionEvent

## Identity API

- `POST /identities` creates an identity
- `GET /identities` lists identities
- `GET /identities/{identity_id}` returns one identity or `404`

Example:

```bash
curl -X POST http://127.0.0.1:8000/identities \
  -H "Content-Type: application/json" \
  -d '{
    "id": "00000000-0000-4000-8000-000000000001",
    "display_name": "Ivan Petrov",
    "first_name": "Ivan",
    "last_name": "Petrov",
    "birth_date": "1990-01-01",
    "documents": [
      {
        "id": "00000000-0000-4000-8000-000000000002",
        "type": "internal_passport",
        "number": "1234567890"
      }
    ]
  }'
```

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
