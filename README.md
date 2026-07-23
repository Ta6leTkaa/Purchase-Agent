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
    "display_name": "Ivan Petrov",
    "first_name": "Ivan",
    "last_name": "Petrov",
    "birth_date": "1990-01-01",
    "documents": [
      {
        "type": "internal_passport",
        "number": "1234567890"
      }
    ]
  }'
```

## Mission API

- `POST /missions` creates a mission
- `GET /missions` lists missions
- `GET /missions/{mission_id}` returns one mission or `404`

Example:

```bash
curl -X POST http://127.0.0.1:8000/missions \
  -H "Content-Type: application/json" \
  -d '{
    "type": "train_trip",
    "title": "Moscow to Saint Petersburg",
    "participant_ids": ["00000000-0000-4000-8000-000000000001"],
    "provider": "rzd",
    "constraints": {
      "from_city": "Moscow",
      "to_city": "Saint Petersburg",
      "travel_date": "2026-08-01",
      "passengers_count": 1
    }
  }'
```

Create endpoints accept only data for the new entity. Identifiers and internal
state such as mission status, execution log, and best option are created by the
server.

Mission creation requires existing Identity ids. `participant_ids` must be
unique, and the number of participants must match `passengers_count`.

Scheduled mission example:

```bash
curl -X POST http://127.0.0.1:8000/missions \
  -H "Content-Type: application/json" \
  -d '{
    "type": "train_trip",
    "title": "Scheduled family train trip",
    "participant_ids": ["00000000-0000-4000-8000-000000000001"],
    "provider": "mock_train",
    "scheduled_at": "2026-08-01T10:00:00Z",
    "constraints": {
      "from_city": "Moscow",
      "to_city": "Saint Petersburg",
      "travel_date": "2026-08-01",
      "passengers_count": 1
    }
  }'
```

There is no automatic scheduler yet. A scheduled mission is stored in
`waiting` status and still has to be started through the run API after
`scheduled_at`.

Repositories can query missions that are due for scheduled execution. This is
only preparation for a future background worker; no scheduler, polling loop, or
automatic `run_mission` call exists yet.

## Due mission processor

The due mission processor performs one pass over missions whose scheduled time
has arrived and runs them sequentially. It is only a programmatic service for
now; a persistent background scheduler will be added separately.

## Mission claiming

Before execution, each due mission is atomically claimed by moving it from
`waiting` to `processing` and setting `claimed_at` to the claim time.
PostgreSQL claiming uses `FOR UPDATE SKIP LOCKED`, so multiple processing
cycles can run without selecting the same mission at the same time.

This prevents concurrent processing of one mission, but it is not a full
exactly-once guarantee. After normal completion, `claimed_at` is cleared. A
mission left in `processing` with `claimed_at` may be stuck; automatic detection
and recovery for that case will be added separately.

## Execution attempts

`execution_attempts` counts successful claim operations, not provider calls or
internal execution steps. A transition from `waiting` to `processing` increases
the counter; stale recovery keeps its value unchanged. Manual execution from
`created` does not count as an attempt yet.

```text
created: attempts=0
scheduled waiting: attempts=0
first claim: attempts=1
stale recovery: attempts=1
second claim: attempts=2
```

Attempt limits and retry policy will be added separately.

## Mission types

Missions now expose a canonical `mission_type`. Currently the only supported
type is `train_ticket`. Future types will include `flight_ticket`,
`hotel_booking`, `event_ticket`, `appointment`, `visa`, and `insurance`.
The Mission Engine currently handles all missions the same way; specialized
behavior will be introduced in later steps.

## Mission payloads

`mission_type` defines a mission's semantics, while `payload` contains its
typed data. Only `train_ticket` is supported today. Its payload is validated
before storage and is persisted as JSONB in PostgreSQL; the domain layer uses a
`TrainTicketMissionPayload` object instead of raw JSON.

```json
{
  "mission_type": "train_ticket",
  "payload": {
    "origin": "Amsterdam",
    "destination": "Berlin",
    "departure_date": "2026-09-15"
  }
}
```

Future mission types will define their own payload models.

## Maximum execution attempts

`max_execution_attempts` limits claims per mission and defaults to `3`. The
last available claim may enter `processing`; if it later becomes stale, recovery
marks the mission as `failed`. Exhausted missions in `waiting` are not claimed.
Manual execution from `created` remains outside this limit for now.

```text
max=2, attempts=0
first claim -> attempts=1
stale recovery -> waiting
second claim -> attempts=2
stale recovery -> failed
```

Backoff and retry scheduling are not implemented yet.

## Stale processing missions

A stale mission is a mission in `processing` whose `claimed_at` is older than a
chosen timeout. Repositories can diagnose these records with a read-only query:

```python
from datetime import timedelta

stale_missions = await mission_repository.list_stale_processing(
    current_time=clock.now(),
    claim_timeout=timedelta(minutes=15),
    limit=100,
)
```

Missions with `claimed_at=None` are not returned or recovered automatically.
Recovery and retry behavior will be added in a separate step.

## Stale mission recovery

The repository can atomically recover stale missions from `processing` to
`waiting`. PostgreSQL uses `FOR UPDATE SKIP LOCKED`; recovery clears
`claimed_at` and records a `claim_recovered` event in the mission log.

Recovery does not start a mission in the same operation. A later processing
cycle can claim the recovered mission again. Retry policy and retry limits are
not implemented yet.

Manual admin processing endpoint:

```bash
curl -X POST http://127.0.0.1:8000/admin/missions/process-due \
  -H "X-Admin-API-Key: replace-with-a-long-random-value" \
  -H "Content-Type: application/json" \
  -d '{"limit": 100}'
```

This endpoint runs only one processing pass. It is intended for local
development and manual checks; it is not a scheduler. A future background
worker should replace this manual trigger.

## Admin stale recovery

Run one stale mission recovery cycle through the protected admin endpoint:

```bash
curl -X POST \
  -H "X-Admin-API-Key: replace-with-a-long-random-value" \
  -H "Content-Type: application/json" \
  -d '{
    "claim_timeout_seconds": 900,
    "limit": 100
  }' \
  http://localhost:8000/admin/missions/recover-stale
```

The endpoint returns stale missions from `processing` to `waiting` without
starting them. Call `/admin/missions/process-due` separately to process a
recovered mission. It is protected by the admin API key; concurrent recovery
requests are safe because the repository uses `SKIP LOCKED`.

## CLI processing

Due missions can also be processed without starting the FastAPI server:

```bash
python -m app.cli process-due
python -m app.cli process-due --limit 50
```

The command runs exactly one processing cycle, uses `DATABASE_URL` from
configuration, and writes a JSON result to stdout. It can be run manually today
and later called by cron or another external scheduler. The CLI itself does not
contain a polling loop.

## CLI stale recovery

Recover stale processing missions without starting the FastAPI server:

```bash
python -m app.cli recover-stale
python -m app.cli recover-stale \
  --claim-timeout-seconds 1800 \
  --limit 50
```

The timeout defines the maximum acceptable claim age. The command performs one
recovery cycle, returns matching missions to `waiting`, and does not run them.
Run `process-due` separately to claim and execute them. PostgreSQL uses
`SKIP LOCKED`, so multiple recovery processes can safely run at once.

## Admin API key

Administrative endpoints require `X-Admin-API-Key`. The key protects only
admin routes and is a temporary local-development guard until full
authentication exists.

```bash
curl -X POST http://localhost:8000/admin/missions/process-due \
  -H "X-Admin-API-Key: replace-with-a-long-random-value" \
  -H "Content-Type: application/json" \
  -d '{"limit": 100}'
```

Set `ADMIN_API_KEY` through environment or secret management. Do not store real
keys in Git.

## Provider adapters

The backend includes a `ProviderAdapter` interface and a `MockTrainAdapter`
for local development. `MockTrainAdapter` does not call real booking websites;
it returns deterministic train options so the core matching and mission logic
can be developed later.

## Rule Engine

The Rule Engine deterministically scores train options against mission
constraints and fallback rules. It returns scored options with reasons and
violations, ordered so valid options are considered before violated ones.

## Mission Engine

The Mission Engine runs a mission through repositories, `MockTrainAdapter`, and
Rule Engine. Execution currently stops at `requires_confirmation`; it does not
perform automatic payment or call real booking websites.

Example:

```bash
curl -X POST http://127.0.0.1:8000/missions/{mission_id}/run
```

Mission execution is not a repeatable operation. Re-running a mission from an
active or terminal status returns HTTP `409`. A future new attempt should use a
new Mission or a dedicated retry mechanism.

Confirm a mission waiting for user confirmation:

```bash
curl -X POST http://127.0.0.1:8000/missions/{mission_id}/confirm
```

The confirmation endpoint only simulates user confirmation. It does not perform
real payment or call booking websites. `completed` means the mock scenario has
finished successfully.

## Mission state machine

Mission statuses are changed through explicit valid transitions. `completed`
and `failed` are terminal statuses. The state machine does not write execution
events and does not handle persistence; Mission Engine and repositories remain
responsible for those concerns.

## Repository abstraction

API routes and services depend on repository interfaces instead of a concrete
storage implementation. The current repositories are in-memory, and can later
be replaced with PostgreSQL-backed repositories without changing API behavior.
There is also a PostgreSQL/SQLAlchemy implementation of `IdentityRepository`,
and `MissionRepository`, but the application uses the in-memory repositories by
default. Identity and Mission now both have in-memory and SQLAlchemy
implementations. SQLAlchemy repositories flush changes but do not commit
transactions themselves; transaction boundaries are owned by the outer layer.

## Storage backend

The application uses in-memory repositories by default:

```bash
STORAGE_BACKEND=memory
```

To use SQLAlchemy repositories, set the database backend and database URL:

```bash
STORAGE_BACKEND=database
DATABASE_URL=postgresql+asyncpg://purchase_agent:purchase_agent@localhost:5432/\
purchase_agent
```

The database backend requires applied Alembic migrations.

## Local PostgreSQL

The application still runs locally through `uv`; only PostgreSQL runs in
Docker. You can keep `STORAGE_BACKEND=memory` for the default in-memory
development mode. To use `STORAGE_BACKEND=database`, start PostgreSQL first and
apply Alembic migrations.

Start PostgreSQL:

```bash
docker compose up -d postgres
```

Check container status:

```bash
docker compose ps
```

Create a test database for PostgreSQL integration tests:

```bash
docker compose exec postgres createdb -U purchase_agent purchase_agent_test
```

Apply migrations:

```bash
uv run alembic upgrade head
```

Stop PostgreSQL:

```bash
docker compose down
```

Remove PostgreSQL data completely:

```bash
docker compose down -v
```

## Database infrastructure

PostgreSQL, SQLAlchemy, and Alembic infrastructure is prepared. The application
still uses in-memory repositories.

The database layer currently includes ORM models for Identity, Document, and
Mission, plus Alembic migrations for their tables. Mission nested structures
such as constraints, fallback rules, execution events, and provider options are
temporarily stored as JSON. PostgreSQL repositories and additional ORM models
will be added in separate steps; the API still uses the in-memory
`MissionRepository`.

Create local database settings from the example file:

```bash
cp .env.example .env
```

Inspect pending migrations:

```bash
uv run alembic history
```

Apply migrations:

```bash
uv run alembic upgrade head
```

## Provider capabilities

`ProviderCapability` declaratively identifies the `MissionType` an adapter can
handle. Capabilities are immutable, and `MissionEngine` verifies compatibility
before it searches or reserves through a provider.

## Provider registry

`ProviderRegistry` is an immutable application-level catalog of configured
provider adapter instances. It supports exact lookup by stable `provider_id`
and filters adapters through `supports()`, preserving registration order.
Duplicate identifiers are rejected. The registry does not select a provider;
automatic routing will be added separately through a Provider Resolver.

## Provider discovery

The read-only provider discovery API exposes the provider adapters configured
in the current runtime registry:

- `GET /providers`
- `GET /providers/supporting/{mission_type}`

For example, a client can query `GET /providers/supporting/train_ticket`, use a
returned machine-readable `provider_id` in `Mission.provider_id`, and then let
`MissionEngine` resolve that explicit selection during execution. Empty lists
are valid `200 OK` responses. Discovery does not run live availability checks,
provider operations, or health checks; it only reports declared capabilities.

## Explicit provider selection

A Mission may optionally carry `provider_id` as an explicit provider selection.
`None` means no provider has been selected. The value is persisted as mission
intent and matches the stable `ProviderAdapter.provider_id` contract, but it is
not resolved or capability-checked during creation. Provider Registry and
Mission Engine behavior remain unchanged until a separate Provider Resolver is
introduced.

`resolved_provider_id` is separate execution metadata: it records the adapter
actually chosen by `ProviderResolver` for the latest attempt. Automatic
selection leaves `provider_id` as `None` and sets `resolved_provider_id` before
the first provider side effect. Clients cannot supply this field on creation.

Provider selection can be changed before execution with
`PUT /missions/{mission_id}/provider`. A non-null `provider_id` must be a
registered adapter that supports the mission type; `null` returns the mission
to automatic selection. When automatic resolution is ambiguous, the client can:

1. Query `GET /providers/supporting/{mission_type}`.
2. Choose a returned `provider_id`.
3. Set it with `PUT /missions/{mission_id}/provider`.
4. Run the mission again.

The update validates selection but does not resolve or execute the mission.
Changing the requested value clears `resolved_provider_id`; an idempotent update
keeps existing resolved metadata, and historical execution events remain intact.
Each actual change records `provider_selection_changed` with the previous and
new requested provider IDs and their automatic/explicit selection modes. A
repeated request with the same normalized ID is a no-op and records no event.
This keeps the audit trail intact across the sequence
`provider_resolution_failed`, `provider_selection_changed`, and
`provider_resolved`.

Each successful resolution also records a persistent `provider_resolved` entry
in the existing Mission execution log before provider operations start. Its
metadata contains the resolved provider ID, mission type, and whether selection
was explicit or automatic.

Expected resolution failures record `provider_resolution_failed` before the
typed error is re-raised. The event stores a stable reason code, mission type,
the requested provider when present, and ambiguity candidates when applicable;
it never sets `resolved_provider_id`.

## Provider Resolution HTTP Errors

Provider resolution failures are persisted by Mission Engine and then mapped by
global API handlers. The handlers do not retry, choose fallbacks, or create
additional events.

| Application error | HTTP status | API code |
| --- | ---: | --- |
| `UnknownProviderError` | 422 | `unknown_provider` |
| `UnsupportedMissionTypeError` | 422 | `unsupported_mission_type` |
| `NoSupportingProviderError` | 409 | `no_supporting_provider` |
| `AmbiguousProviderError` | 409 | `ambiguous_provider` |

## Provider resolver

`ProviderResolver` applies deterministic provider selection without invoking
provider operations or changing a Mission. An explicit `provider_id` is looked
up exactly and must support the mission type. Without one, exactly one
supporting adapter is selected; zero or multiple matches raise typed errors.
The resolver has no priorities or fallback.

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

Integration tests are excluded from the default test run. To make that explicit:

```bash
uv run pytest -m "not integration"
```

Run PostgreSQL integration tests after starting local PostgreSQL and creating
the test database. These tests cover `IdentityRepository` and
`MissionRepository`, plus end-to-end `Identity API` and `Mission API`
persistence paths through FastAPI dependencies and PostgreSQL. They also cover
the full mission execution flow through PostgreSQL repositories, `MockTrainAdapter`,
and Rule Engine. Real browser automation is not included yet. Regular unit
tests do not require PostgreSQL.

```bash
TEST_DATABASE_URL=postgresql+asyncpg://purchase_agent:purchase_agent@localhost:5432/\
purchase_agent_test uv run pytest -m integration
```

## Run linting

```bash
uv run ruff check .
```

## Run type checks

```bash
uv run mypy
```
