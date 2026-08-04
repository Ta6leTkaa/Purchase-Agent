# Purchase Agent

Purchase Agent is a FastAPI backend for scheduled purchase missions. It ships
with typed train-ticket missions, provider resolution, a deterministic mock
provider, durable Mission events, PostgreSQL persistence, and read-only audit
APIs for provider resolution history.

## Local Compose deployment

Copy the tracked configuration template, replace both API keys with different
random values of at least 32 characters, then start the API stack:

```bash
cp .env.example .env
docker compose up --build
```

The Compose API runs with `ENVIRONMENT=production` and fails closed unless it
uses database storage, disables debug and public API docs, and receives two
different API keys of at least 32 characters each. Direct local development
keeps `ENVIRONMENT=local` defaults. This prevents an incomplete secret or an
accidentally enabled development surface from reaching production traffic.

Compose starts PostgreSQL, runs `alembic upgrade head` once, and only then
starts the API at `http://localhost:8000`. `GET /health` is a liveness check;
`GET /ready` also verifies the configured storage and is used by Compose before
traffic is considered safe. Database readiness includes an exact Alembic head
check, so an instance with missing, outdated, or split schema revisions does
not receive traffic. Scheduled execution and notification delivery remain opt-in:

After deployment, run the non-mutating smoke check. It verifies liveness,
readiness, client authentication, admin authentication, and that the instance
is accepting traffic; output is one JSON report and secrets are never echoed:

```bash
API_KEY=... ADMIN_API_KEY=... python -m app.cli smoke-api \
  --base-url https://purchase-agent.example.com
```

On a disposable or staging database, validate the complete mutating lifecycle:

```bash
API_KEY=... python -m app.cli smoke-flow \
  --base-url https://purchase-agent.example.com \
  --provider-id mock_train
```

This creates a synthetic passenger and Mission, then searches, reserves,
confirms, and verifies a successful terminal outcome. It deliberately persists
those audit records and must not be used as a read-only production probe.

```bash
docker compose --profile worker --profile notifications up --build
```

Use `API_PORT` and `POSTGRES_PORT` when their defaults are occupied. Compose
does not set a global container name, so isolated stacks can run concurrently:

```bash
API_PORT=58000 POSTGRES_PORT=55432 \
  docker compose -p purchase-agent-staging \
  --profile worker --profile notifications up -d --build
docker compose -p purchase-agent-staging ps
```

All long-running services must become `healthy`; `migrate` must exit with code
zero. Then run `smoke-api`, and use `smoke-flow` only against disposable or
staging data. Inspect failures with `docker compose logs api worker
notification-worker migrate`.

## Operations runbook

Create a PostgreSQL backup before an upgrade that includes migrations:

```bash
docker compose exec -T postgres pg_dump \
  -U purchase_agent -d purchase_agent -Fc > purchase-agent.dump
```

Verify that the dump is non-empty and store it outside the Compose volume. To
restore into an intentionally empty recovery database, stop API and workers,
recreate that database, and run:

```bash
docker compose exec -T postgres pg_restore \
  -U purchase_agent -d purchase_agent --clean --if-exists < purchase-agent.dump
```

For an update, drain the API, create the backup, pull/build the new image, run
`docker compose run --rm migrate`, and only then recreate API and worker
services. Confirm `/ready`, `smoke-api`, and `/admin/worker-health` before
returning traffic. Application rollback means restoring the previous image;
database downgrade is not automatic and should use the verified backup when a
migration is incompatible. Preserve API, worker, and PostgreSQL logs during an
incident, but never include `.env`, API keys, provider tokens, passenger
documents, or notification payloads in a support bundle.

Client-facing Identity, Mission, and Provider endpoints require the configured
key in the `X-API-Key` header. Liveness and readiness probes remain public.
Local development without `API_KEY` preserves unauthenticated access. Admin
endpoints use the separate `X-Admin-API-Key` header.
Both keys are published as distinct OpenAPI security schemes, so Swagger UI
and generated clients can authenticate without treating secrets as ordinary
request parameters. Missing configured keys return an `ApiKey`
`WWW-Authenticate` challenge.

Framework-level request validation failures use a stable
`422 request_validation_error` envelope. Each issue contains only its location,
type, and safe message; rejected input values are deliberately omitted. The
envelope includes the request ID so clients can correlate a validation failure
with structured server logs. Domain errors retain their more specific codes.

All API responses set `nosniff`, deny framing, suppress referrer information,
and disable camera, microphone, and geolocation browser capabilities. Error
responses default to `Cache-Control: no-store`. Interactive docs remain enabled
for direct local development; Compose disables `/docs`, `/redoc`, and
`/openapi.json` by default. Set `API_DOCS_ENABLED=true` only where public API
documentation is intended.

Browser clients on another origin require an explicit JSON allowlist, for
example `CORS_ALLOWED_ORIGINS=["http://localhost:3000"]`. Wildcards,
credentials, paths, and insecure non-local origins are rejected. Preflight
requests allow the API key, admin key, idempotency, concurrency, and request-ID
headers; browser code may read `ETag` and `X-Request-ID` response headers.

Individual Identity and Mission reads support `If-None-Match`. Send the ETag
from the previous response to receive `304 Not Modified` without another JSON
payload when the resource version has not changed. Weak tags, tag lists, and
the wildcard form follow HTTP cache-validation semantics. Detail responses use
`Cache-Control: private, no-cache` so shared caches do not retain participant
or mission data and browsers revalidate before reuse.

Request bodies are capped at 1 MiB by default, before JSON parsing or route
execution. Set `MAX_REQUEST_BODY_BYTES` to a value from 1024 bytes through
100 MiB when a deployment needs a different bound. Both declared
`Content-Length` and incrementally received bodies are enforced; oversized
requests return `413 request_body_too_large` with the configured byte limit.

Every HTTP request also has a 60-second processing deadline, configurable with
`REQUEST_TIMEOUT_SECONDS` from above 30 seconds through 15 minutes. The lower
bound leaves room for the API's supported 30-second long polls. A deadline
cancels in-flight application work so dependency cleanup and database rollback
can run, then returns `504 request_timeout` if the response has not started.

Production API traffic is limited to 120 requests per rolling 60-second
window by default. Compose enables the limiter with
`API_RATE_LIMIT_ENABLED=true`; production configuration fails closed if it is
disabled, while direct local/test applications keep it opt-in. Client and
admin API keys receive independent buckets; keys
are represented internally only by SHA-256 digests. Unauthenticated traffic is
bucketed by peer address, while health probes and CORS preflight remain exempt.
Rejected requests return `429 rate_limit_exceeded` with `Retry-After` and
`RateLimit-*` headers. Tune `API_RATE_LIMIT_REQUESTS`,
`API_RATE_LIMIT_WINDOW_SECONDS`, and the bounded
`API_RATE_LIMIT_MAX_CLIENTS` registry for each replica's capacity.

Every HTTP response includes an `X-Request-ID`. A caller-provided identifier is
preserved when it contains only safe correlation characters and is at most 128
characters; otherwise the API generates a UUID. Each request produces a JSON
log record with this identifier, method, path, status, and duration. Headers,
query strings, API keys, and request bodies are deliberately excluded.

`GET /admin/http-statistics` exposes process-local aggregate traffic counters
under the admin API key: completed and in-flight requests, method and status
class counts, and average/maximum latency. It deliberately omits paths, query
strings, headers, and bodies to avoid high-cardinality data and secret leakage.
Counters reset when the API process restarts and should be aggregated per
replica by deployment-level monitoring.

`GET /admin/mission-statistics` provides an operator-facing snapshot of the
mission queue for both memory and PostgreSQL storage. It reports totals by
status together with due, expired-but-not-yet-processed, stale-processing, and
attempt-exhausted waiting counts. The optional `claim_timeout_seconds` query
parameter controls when a processing claim is considered stale.

Before restarting an API replica, call `POST /admin/runtime/drain` with the
admin key. The process immediately reports `503 instance_draining` from
`/ready` while `/health` and already accepted work remain available. Repeated
drain calls are idempotent and preserve the original timestamp. Inspect the
state through `GET /admin/runtime-status`; use `POST /admin/runtime/resume` to
return a replica to readiness if the restart is cancelled. Drain state is
process-local and resets when the process restarts.

## Continuous integration

The repository includes a GitHub Actions workflow in
`.github/workflows/ci.yml`. It runs Ruff, mypy, the unit suite, bytecode
compilation, verifies that Alembic has exactly one head, validates Compose, and
builds the production image on every push and pull request. A separate job
starts PostgreSQL 16, applies migrations to an empty database, verifies the
migration head, and runs the complete integration suite. The final gate builds
the image again, migrates a clean PostgreSQL database through that image,
starts the API with fail-closed production settings, waits for readiness, runs
the non-mutating smoke command, executes a PostgreSQL-backed Mission lifecycle,
starts both background workers, verifies their persistent heartbeats, and
confirms that public OpenAPI is disabled.

## External train provider

The deterministic `mock_train` provider remains available for development.
Set `TRAIN_PROVIDER_BASE_URL` to register the production `http_train` adapter
alongside it, then select `provider_id: "http_train"` on a Mission. Non-local
provider URLs must use HTTPS. Optional `TRAIN_PROVIDER_BEARER_TOKEN` is sent as
a bearer credential and `TRAIN_PROVIDER_TIMEOUT_SECONDS` defaults to 15.

The upstream gateway contract consists of four JSON endpoints:

- `POST /v1/train/options/search` receives the Mission route, constraints, and
  typed passenger records and returns `{ "options": [...] }`;
- `POST /v1/train/reservations` receives the selected option;
- `POST /v1/train/reservations/{id}/confirm` confirms the hold;
- `POST /v1/train/reservations/{id}/cancel` releases it.

All reservation mutations receive the engine's `Idempotency-Key`. Responses
use the existing `ProviderOption`, `ReservationResult`, `ConfirmationResult`,
and `CancellationResult` schemas. The adapter never records upstream response
bodies in raised errors; timeouts, network failures, `429`, and `5xx` responses
are retryable, while other `4xx` responses are terminal. Passenger identity and
document data crosses this boundary, so the configured gateway must be trusted
and must not log request bodies.

Clients can drive the complete flow without interpreting internal transition
states. `GET /missions/{mission_id}/outcome` returns the current status,
selected option and reservation together with `terminal`, `successful`, and an
explicit `next_action` (`run`, `wait`, `resume`, `confirm`, `retry`, or `none`).
Mutation calls should carry the latest Mission ETag in `If-Match` and a stable
`Idempotency-Key`; safe retries then return the original result without another
provider operation.

## Domain models

The backend includes initial Pydantic domain models for:

- Identity
- Mission
- ProviderOption
- ExecutionEvent

## Identity API

- `POST /identities` creates an identity
- `GET /identities` lists identities
- `GET /identities/summaries` lists only identity ids and display names
- `GET /identities/summaries/page` pages through identity summaries
- `GET /identities/{identity_id}` returns one identity or `404`
- `PATCH /identities/{identity_id}` updates profile fields and documents
- `PUT /identities/{identity_id}/preferences` replaces train and notification preferences
- `DELETE /identities/{identity_id}` removes an unused identity and its documents

Creation accepts an optional `Idempotency-Key` header (1–255 characters).
Repeating the same validated payload under that key returns the original
Identity without creating another record. Reusing it with a different payload
returns `409 idempotency_key_conflict`.

Identity listing accepts `q` for case-insensitive display/first/last-name
search and `limit` from 1 to 500. It never searches document numbers; the
response remains a JSON array. The summary endpoint accepts the same filters
without loading documents, birth dates, or preferences.
An identity referenced by a Mission cannot be deleted: the API returns
`409 identity_in_use` and keeps the participant record intact.
Profile updates are partial: omitted fields stay unchanged, while a supplied
`documents` array replaces the complete document collection with newly
generated document ids. Empty updates, nulls, blank names, and unknown fields
are rejected.
Individual Identity responses include an `ETag` backed by a persisted numeric
version. `PATCH`, preference replacement, and deletion require the latest
quoted value in `If-Match`; missing preconditions return `428`, while stale
versions return `412 identity_version_conflict`. Successful updates increment
both the response `version` and `ETag`.
The paged endpoint returns `items`, `has_more`, and an opaque `next_cursor`.
Pass the cursor unchanged with the same `q` filter to continue the stable,
exclusive traversal.

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
- `GET /missions/summaries/page` pages through lightweight mission summaries
- `GET /missions/{mission_id}` returns one mission or `404`
- `PATCH /missions/{mission_id}` changes safe planning fields
- `POST /missions/{mission_id}/pause` pauses an unstarted mission
- `POST /missions/{mission_id}/resume` resumes a paused mission

Mission creation supports the same optional `Idempotency-Key` contract. Keys
are scoped by resource type, so a client may use the same value independently
for one Identity creation and one Mission creation. PostgreSQL persists
receipts across API restarts; concurrent unfinished retries receive
`409 idempotent_request_in_progress`.

Completed creation receipts can be retained for a bounded period and then
removed in batches without touching in-progress requests:

```bash
python -m app.cli prune-creation-receipts \
  --retention-days 30 \
  --limit 500 \
  --dry-run
```

Remove `--dry-run` to commit the deletion. A partial PostgreSQL index covers
only completed receipts and keeps retention scans bounded.

Mission listing accepts optional `status`, `type`, and `limit` query
parameters. `limit` defaults to `100` and is capped at `500`; the response
remains a JSON array for compatibility. Composite indexes cover general,
status-filtered, and type-filtered ordering; separate indexes support due and
stale worker claims.

`GET /missions/summaries` accepts the same filters and returns a lightweight
projection for list screens. It includes status, scheduling, provider and
attempt counters, but omits participant ids, constraints, provider options and
the complete execution log. Use `GET /missions/{mission_id}` when the full
aggregate is required.
The paged summary response contains `items`, `has_more`, and an opaque
`next_cursor`. Repeat the same `status` and `type` filters when continuing a
stable exclusive traversal.

Example:

```bash
curl -X POST http://127.0.0.1:8000/missions \
  -H "Content-Type: application/json" \
  -d '{
    "mission_type": "train_ticket",
    "title": "Moscow to Saint Petersburg",
    "participant_ids": ["00000000-0000-4000-8000-000000000001"],
    "provider": "mock_train",
    "payload": {
      "origin": "Moscow",
      "destination": "Saint Petersburg",
      "departure_date": "2026-08-01"
    },
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
An optional timezone-aware `expires_at` defines the last instant at which the
Mission may start. When both values are present, `scheduled_at` must not be
later than `expires_at`; at an equal timestamp expiration is processed before
claiming.

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

A scheduled mission is stored in `waiting` status. It can be processed by the
one-shot due processor, the continuous worker, or started explicitly through
the run API after `scheduled_at`.

An unstarted Mission can be scheduled or rescheduled without executing it:

```bash
curl -X PUT http://127.0.0.1:8000/missions/{mission_id}/schedule \
  -H "Content-Type: application/json" \
  -d '{"scheduled_at":"2030-08-01T10:00:00Z"}'
```

Only `created`, `waiting`, and `paused` Missions accept schedule changes.
Scheduling a created Mission moves it to `waiting`; sending
`{"scheduled_at": null}` for a waiting Mission removes its schedule and returns
it to `created`. A paused Mission stays paused while its schedule is edited.
All actual changes record an audit event. This endpoint does not trigger an
immediate run.

## Updating Mission configuration

Safe planning fields can be changed before execution:

```bash
curl -X PATCH http://127.0.0.1:8000/missions/{mission_id} \
  -H "Content-Type: application/json" \
  -H 'If-Match: "0"' \
  -d '{
    "title": "Updated journey",
    "execution_mode": "search_only",
    "max_execution_attempts": 5,
    "fallback_rules": {"allow_any_coupe_seats": true}
  }'
```

The endpoint accepts any non-empty subset of `title`, `fallback_rules`,
`execution_mode`, and `max_execution_attempts`. It is limited to `created` and
`waiting` Missions, rejects an attempt limit below the number already used,
and verifies that an explicitly selected provider supports a changed execution
mode. Successful changes produce one `mission_updated` audit event and a new
`ETag`; an unchanged request is an idempotent no-op. `If-Match` is optional,
but clients should send the last Mission `ETag` to prevent lost updates.

## Pausing and resuming Missions

An unstarted Mission can be taken out of processing without deleting its
schedule or configuration:

```bash
curl -X POST http://127.0.0.1:8000/missions/{mission_id}/pause \
  -H "Idempotency-Key: pause-unique-key" \
  -H 'If-Match: "3"'

curl -X POST http://127.0.0.1:8000/missions/{mission_id}/resume \
  -H "Idempotency-Key: resume-unique-key" \
  -H 'If-Match: "4"'
```

Only `created` and `waiting` Missions can be paused. A paused Mission is
excluded from due claims and cannot be run. It can still be reconfigured,
rescheduled, assigned another provider, or cancelled. Resuming restores
`waiting` when a schedule exists and `created` otherwise. Both commands are
idempotency-key protected, support optimistic version checks, return a new
`ETag`, and append `mission_paused` or `mission_resumed` to the audit history.

Repositories query missions that are due for scheduled execution. Processing is
triggered explicitly through the admin endpoint, a one-shot CLI command, or
the continuous worker CLI.

## Due mission processor

The due mission processor performs one pass over missions whose scheduled time
has arrived and runs them sequentially. It is exposed through protected admin
and CLI commands and is also used by the continuous worker.
Its result separates terminal `failed_mission_ids` from
`retry_scheduled_mission_ids`, allowing operators to distinguish permanent
failures from temporary provider outages.

## Mission claiming

Before execution, each due mission is atomically claimed by moving it from
`waiting` to `processing` and setting `claimed_at` to the claim time.
PostgreSQL claiming uses `FOR UPDATE SKIP LOCKED`, so multiple processing
cycles can run without selecting the same mission at the same time.

This prevents concurrent processing of one mission, but it is not a full
exactly-once guarantee. After normal completion, `claimed_at` is cleared. A
mission left in `processing` with `claimed_at` can be inspected and explicitly
recovered through repository, CLI, or protected admin recovery operations.

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

Retryable provider failures discovered by `process-due` are automatically
rescheduled with bounded exponential backoff. The default delays are 30, 60,
120 seconds and continue doubling up to 15 minutes. Each claim still consumes
one execution attempt. When `max_execution_attempts` is reached, the Mission
remains `failed` and is not scheduled again. Provider adapters can mark an
operation error as non-retryable to fail immediately.

An empty search or a result set where every option violates mandatory
constraints is treated as an availability miss rather than a permanent
failure. The worker schedules another search using the same bounded backoff,
up to `max_execution_attempts`. If the calculated retry would cross
`expires_at`, it is capped at the deadline; expiration runs before claiming,
so the Mission ends as `expired` without an extra provider call.

Before each due-processing pass, unstarted `created`, `waiting`, and `paused`
Missions whose `expires_at` has arrived are moved to the terminal `expired`
state. Expiration records `mission_expired`, consumes no execution attempt, and
is reported separately in `expired_mission_ids`. Repository claim queries also
exclude expired deadlines, preventing a race from starting stale work.

## Notification outbox

User-facing lifecycle events are copied to `notification_outbox` in the same
database transaction as the canonical Mission event. The initial event set is:

- `waiting_for_user_confirmation`;
- `mission_completed`;
- `mission_failed`;
- `mission_cancelled`;
- `mission_expired`.

Each outbox record also snapshots the Mission `participant_ids` as
`recipient_ids`. This keeps the event-to-recipient relationship immutable even
if participants are changed later; a notification gateway can route by these
identity ids without receiving passport or preference data.

This provides an at-least-once delivery boundary without making a provider
operation depend on email, messenger, or webhook availability. Outbox rows use
the immutable Mission `event_id` as a uniqueness boundary. Concurrent
dispatchers claim pending rows with `FOR UPDATE SKIP LOCKED`.

The first delivery adapter writes JSON Lines to stdout, making the contract
observable and allowing a process supervisor or log pipeline to consume it:

```bash
python -m app.cli dispatch-notifications --limit 100
```

Successful delivery marks the row `delivered`. Transient adapter failures are
returned to `pending` with exponential delays of 30, 60, 120 seconds and so on,
capped at 15 minutes. A valid receiver-provided `Retry-After` delay takes
precedence. After five delivery attempts the row is retained as `failed` for
operator inspection. Webhook responses `408`, `425`, `429`, and `5xx` are
retryable; other `3xx` and `4xx` responses fail immediately because repeating
the same request cannot normally correct them. The stdout adapter is a safe
demonstration transport. Set `NOTIFICATION_WEBHOOK_URL` to use the included
webhook transport instead: it POSTs the full outbox JSON, uses the immutable
event id in `Idempotency-Key`, and adds `Authorization: Bearer …` when
`NOTIFICATION_WEBHOOK_BEARER_TOKEN` is configured. A non-2xx response enters
the normal retry flow. With `NOTIFICATION_WEBHOOK_SIGNING_SECRET`, the adapter
also adds `X-Purchase-Agent-Signature: sha256=<HMAC-SHA256(body)>` for receiver
verification. `NOTIFICATION_WEBHOOK_TIMEOUT_SECONDS` defaults to 10.
Webhook requests declare `Content-Type: application/json` and
`X-Purchase-Agent-Delivery-Version: 1`. When recipient routing finds no active
contact, the outbox item is completed without making an external request.
External webhook URLs must use HTTPS. Plain HTTP is accepted only for
`localhost`, `127.0.0.1`, and `::1`; an empty URL keeps JSONL delivery enabled.

For continuous delivery, run:

```bash
python -m app.cli notification-worker \
  --poll-interval-seconds 5 \
  --claim-timeout-seconds 300 \
  --limit 100
```

Every cycle first recovers `processing` messages whose dispatcher claim is
older than the configured timeout, then delivers the pending batch. Recovery
preserves the delivery-attempt counter and makes the message immediately
available, providing at-least-once behavior after a process crash. Multiple
notification workers can run concurrently.

Operators can inspect the backlog without exposing notification payloads:

```bash
curl http://127.0.0.1:8000/admin/notification-outbox/statistics \
  -H "X-Admin-API-Key: $ADMIN_API_KEY"
```

The response reports counts by delivery status, the number of pending messages
already eligible for delivery, and the availability timestamp of the oldest
pending message.

Large outboxes can be traversed through
`GET /admin/notification-outbox/page?limit=100`. The response contains
`items`, `has_more`, and an opaque `next_cursor`; pass that cursor unchanged to
the next request. Status and Mission filters remain stable across pages when
the client repeats them. Dedicated composite indexes cover the unfiltered,
status-filtered, and Mission-filtered keyset scans.

An operator can also return abandoned `processing` deliveries to the pending
queue without waiting for the continuous worker:

```bash
curl -X POST \
  http://127.0.0.1:8000/admin/notification-outbox/recover-stale \
  -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"claim_timeout_seconds":300,"limit":100}'
```

Recovery does not increment the delivery-attempt counter and does not perform
delivery in the request. A later notification-worker cycle claims the messages
normally; concurrent recovery calls remain safe through `SKIP LOCKED`.

Delivered outbox history can be removed in bounded maintenance batches:

```bash
python -m app.cli prune-notifications --retention-days 30 --limit 500
```

Add `--dry-run` to preview the same bounded candidate batch without locking or
deleting records.

The command deletes only records whose status is `delivered` and whose
`delivered_at` is older than the calculated cutoff. Pending, processing, and
failed records are always retained. Concurrent cleanup processes divide work
with `SKIP LOCKED`; repeat the command until `deleted_count` becomes zero. A
dedicated `(status, delivered_at, id)` index keeps retention scans bounded as
the outbox grows.

The same worker is available as an opt-in Compose profile:

```bash
docker compose --profile notifications up notification-worker
```

Its settings are `NOTIFICATION_WORKER_POLL_INTERVAL_SECONDS`,
`NOTIFICATION_WORKER_BATCH_SIZE`, and
`NOTIFICATION_CLAIM_TIMEOUT_SECONDS`. Retry behavior is configured with
`NOTIFICATION_RETRY_INITIAL_SECONDS`, `NOTIFICATION_RETRY_MAX_SECONDS`, and
`NOTIFICATION_MAX_DELIVERY_ATTEMPTS`; webhook workers also read
`NOTIFICATION_WEBHOOK_URL`, `NOTIFICATION_WEBHOOK_BEARER_TOKEN`, and
`NOTIFICATION_WEBHOOK_SIGNING_SECRET`, and
`NOTIFICATION_WEBHOOK_TIMEOUT_SECONDS`.

## Retrying failed missions

A failed Mission can be explicitly returned to the due-processing queue while
it still has execution attempts remaining:

```bash
curl -X POST http://127.0.0.1:8000/missions/{mission_id}/retry \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: retry-unique-key" \
  -H 'If-Match: "12"' \
  -d '{"retry_at":"2026-08-01T10:00:00Z"}'
```

Omit `retry_at` to make the Mission immediately eligible for the next
`process-due` cycle. Retrying does not execute the Mission inline and does not
reset `execution_attempts`. It clears stale resolved-provider, reservation, and
best-option data, records `mission_retry_scheduled`, and returns the new Mission
`ETag`. Missions outside `failed` and Missions whose attempt limit is exhausted
return HTTP `409`. The endpoint requires an idempotency key, so replaying the
same successful retry does not create another event.

Automatic retries use the same state transition and event contract, with
`trigger=automatic` and the failure category stored in event metadata. They do
not require an HTTP request or an idempotency key because the claimed attempt
and its persisted sequence provide the processing boundary.

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

Retryable provider failures are rescheduled with bounded exponential backoff as
described above. Manual retries remain available for failed missions.

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
Recovery is an explicit operation and does not retry or execute a Mission.

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
configuration, and writes a JSON result to stdout. It can be run manually or
called by cron when a continuously running worker is not desired.

## Background worker

Run continuous due-Mission processing with a fresh database session for every
cycle:

```bash
python -m app.cli worker
python -m app.cli worker \
  --poll-interval-seconds 2.5 \
  --claim-timeout-seconds 900 \
  --limit 50
```

`WORKER_POLL_INTERVAL_SECONDS`, `WORKER_BATCH_SIZE`, and
`WORKER_CLAIM_TIMEOUT_SECONDS` provide the defaults. Each cycle first recovers
stale claims in one transaction, then processes due Missions in a fresh
transaction. A recovered Mission can therefore run in the same cycle. Exhausted
stale claims are reported separately as `stale_failed_mission_ids`.

The worker releases its transaction and pooled connection before waiting,
continues after an isolated infrastructure failure, and stops gracefully on
`SIGINT` or `SIGTERM`. Each successful cycle writes one structured JSON result
to stdout; safe infrastructure diagnostics go to stderr without including
connection details.

Each successful or failed cycle persists a heartbeat keyed by worker kind and
instance ID. Compose checks it inside each worker container and marks the
service unhealthy when the heartbeat is stale or its latest cycle failed.
Inspect all replicas through `GET /admin/worker-health`; the response contains
timestamps, age, and consecutive failure counts without job payloads.
`WORKER_HEARTBEAT_MAX_AGE_SECONDS` defaults to 60 seconds and should remain
above the poll interval. Override `MISSION_WORKER_INSTANCE_ID` and
`NOTIFICATION_WORKER_INSTANCE_ID` for multiple named replicas.

The worker is also available as an opt-in Docker Compose profile. Apply
migrations before starting it:

```bash
docker compose --profile worker build worker
docker compose --profile worker run --rm worker alembic upgrade head
docker compose --profile worker up -d
```

The profile waits for PostgreSQL health before starting and does not run when
plain `docker compose up` is used.

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
Rule Engine. Every Mission has an explicit execution policy:

- `search_only` selects the best valid offer and completes without reservation;
- `require_confirmation` creates a reservation and always waits for the user;
- `auto_purchase` confirms the mock reservation automatically.

`require_confirmation` is the default for existing and newly created Missions.
`auto_purchase` must be explicitly supplied at creation. It exercises the
provider confirmation contract but does not perform real payment or call real
booking websites.

Providers must also explicitly declare `supports_auto_purchase`. Resolver and
preview reject an `auto_purchase` Mission before any provider operation when
the selected adapter lacks that capability. Provider discovery responses expose
their effective `execution_modes`; adapters that do not opt in remain limited
to `search_only` and `require_confirmation`.

```json
{
  "execution_mode": "search_only"
}
```

Example:

```bash
curl -X POST http://127.0.0.1:8000/missions/{mission_id}/run \
  -H "Idempotency-Key: run-unique-key"
```

Mission execution is not a repeatable operation. Re-running a mission from an
active or terminal status returns HTTP `409`. Failed missions with attempts
remaining can use the dedicated retry endpoint described above.

Confirm a mission waiting for user confirmation:

```bash
curl -X POST http://127.0.0.1:8000/missions/{mission_id}/confirm \
  -H "Idempotency-Key: confirm-unique-key"
```

The confirmation endpoint only simulates user confirmation. It does not perform
real payment or call booking websites. `completed` means the mock scenario has
finished successfully.

## Mission state machine

Mission statuses are changed through explicit valid transitions. `completed`,
`failed`, `cancelled`, and `expired` are terminal statuses. `paused` is a
durable planning state and never participates in due claims. The state machine does not write
execution events and does not handle persistence; application services and
repositories remain responsible for those concerns.

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
- `GET /providers/{provider_id}`

For example, a client can query `GET /providers/supporting/train_ticket`, use a
returned machine-readable `provider_id` in `Mission.provider_id`, and then let
`MissionEngine` resolve that explicit selection during execution. Empty lists
are valid `200 OK` responses. Discovery does not run live availability checks,
provider operations, or health checks; it only reports declared capabilities.

For a preflight check before changing a mission selection, request a specific
provider:

```bash
curl http://127.0.0.1:8000/providers/mock_train
```

```json
{
  "provider_id": "mock_train",
  "mission_types": ["train_ticket"]
}
```

The detail endpoint returns `404` when the provider ID is not registered. Its
response reflects the current runtime registry and does not perform health or
availability checks.

## Mission Provider Resolution Preview

`GET /missions/{mission_id}/provider-resolution` reports the provider resolver
outcome for the Mission's current selection and runtime registry. It can return
`resolved`, `unknown_provider`, `unsupported_mission_type`,
`no_supporting_provider`, or `ambiguous_provider`, all with `200 OK` for an
existing Mission. The endpoint is diagnostic only: it neither executes nor
changes the Mission, writes events, or changes execution attempts. A preview
does not mean the Mission may execute in its current lifecycle status.

## Provider Resolution Snapshot

Every successful provider resolution stores an immutable snapshot in its
`provider_resolved` execution event. The snapshot records the selection mode,
requested and resolved provider IDs, resolver candidates, and mission type at
that moment. It is audit metadata for that execution attempt: it never changes
with later registry updates and is not used by later resolutions. Failed
resolutions continue to use `provider_resolution_failed` without a snapshot.

## Provider Resolution History

`GET /missions/{mission_id}/provider-resolution-history` returns the
chronological provider audit trail recorded for one Mission. It includes only
`provider_resolution_failed`, `provider_selection_changed`, and
`provider_resolved`. It uses ascending cursor pagination, with a default page
size of 50 and a maximum of 100 items:

```bash
curl \
  "/missions/{mission_id}/provider-resolution-history?limit=2"
```

```json
{
  "mission_id": "...",
  "items": [],
  "page": {
    "limit": 2,
    "has_more": true,
    "next_cursor": "..."
  }
}
```

Use the opaque, exclusive cursor for the next page:

```bash
curl \
  "/missions/{mission_id}/provider-resolution-history?limit=2&cursor=..."
```

An existing Mission with no such events returns `200` and an empty list, while
an unknown Mission returns `404`. The canonical ordering permits gaps caused
by unrelated Mission events; no total count is returned, and newly appended
events can appear on later pages. The endpoint reads persisted history only,
does not consult the current provider registry, and does not execute the
Mission or create events. Legacy resolved events may have `snapshot: null`;
newly recorded successful resolutions include their snapshot.

## Mission Event Sequence

Every persisted Mission event has a positive `sequence` that is unique and
strictly increasing within that Mission. `Mission.record_event(...)` assigns
the sequence at append time and persists it together with
`last_event_sequence`; it never derives a sequence from a timestamp or a
current Python list position.

```json
[
  {"sequence": 1, "type": "mission_created"},
  {"sequence": 2, "type": "provider_resolution_failed"},
  {"sequence": 3, "type": "provider_selection_changed"}
]
```

Legacy event arrays are backfilled once by the database migration in their
existing JSON order. The current opaque history cursor remains unchanged:
`event_index` is only its implementation tie-breaker, while `timestamp`
describes event time and `sequence` defines the durable Mission-local order.
The sequence is the ordering primitive used by the separate read-only
incremental history endpoint below.

## Incremental Provider History

`GET /missions/{mission_id}/provider-resolution-history/since/{sequence}`
returns provider-related events whose persisted sequence is strictly greater
than the supplied boundary. It is intended for UI polling, incremental sync,
and audit refresh; it does not execute the Mission or inspect the current
provider registry.

```bash
curl \
  "/missions/{mission_id}/provider-resolution-history/since/12?limit=100"
```

The optional bounded long-poll form waits for newly committed provider events:

```bash
curl -G \
  "/missions/{mission_id}/provider-resolution-history/since/12" \
  --data-urlencode "limit=100" \
  --data-urlencode "wait_seconds=20"
```

```json
{
  "mission_id": "...",
  "since_sequence": 12,
  "latest_sequence": 18,
  "has_more": false,
  "items": [
    {"sequence": 14, "event_type": "provider_selection_changed"},
    {"sequence": 18, "event_type": "provider_resolved"}
  ]
}
```

Incremental history is returned in bounded batches: `limit` defaults to `100`
and is capped at `500`. `has_more` means a further provider event exists after
the final delivered item; clients continue with `since/{latest_sequence}`.
The opaque-cursor history endpoint has its own independent page limit.

`latest_sequence` is the last returned provider-event sequence, or the
requested value when no provider events match. `wait_seconds` defaults to `0`
and is bounded to 30 seconds. The endpoint always reads immediately; it returns
already available batches without waiting, otherwise it rereads fresh persisted
Mission state at a fixed internal interval. Timeout returns `200` with an empty
`items` array and `has_more=false`. Unrelated Mission events do not end the
wait, and request cancellation stops the poll.

Each polling read uses a fresh database session, so its transaction and pooled
connection are released before sleeping. The endpoint currently loads the
Mission's persisted JSON event list and filters it in the application layer; it
is correct for the current storage model but is not an indexed event table scan.
Clients should store `latest_sequence`, process items in ascending sequence
order, then start the next request using that value. Opaque cursor pagination
remains available separately for browsing historical pages and does not support
long polling.

## Provider History Read Projection

PostgreSQL keeps `mission_provider_history_events` as a relational, read-only
projection of provider-related Mission events. Mission `execution_log` JSON
remains the canonical source of truth. Provider event writes update both the
Mission JSON and the projection in one transaction; existing JSON events are
backfilled by the migration.

The projection stores the persisted per-Mission sequence, event timestamp,
typed payload, and the legacy event index required by the existing opaque
history cursor. It supports sequence batches and chronological history pages
without deserializing an entire Mission event list. The projection is rebuildable
from canonical JSON, but this project does not expose a rebuild command or API.

Run the controlled maintenance rebuild with:

```bash
python -m app.cli rebuild-provider-history
```

The command rebuilds the whole projection in one transaction from canonical
Mission JSON. Avoid normal Mission writes while it runs; projection readers may
temporarily observe no history until the transaction commits.

Administrators can verify one Mission without changing either representation:

```text
GET /admin/missions/{mission_id}/provider-history-projection/verification
```

The diagnostic response reports only event counts, missing or unexpected
sequences, and mismatched field names. It never repairs rows or exposes payload
diffs. A detected inconsistency is a successful `200` diagnostic result with
status `inconsistent`; an unknown Mission returns `404`.

## Mission Event Store Abstraction

Application code now has a `MissionEventStore` boundary for loading and
appending canonical Mission events. Today `MissionJsonEventStore` implements
that boundary using the existing Mission JSON event list; sequence allocation
still belongs to `Mission.record_event`. A future canonical event storage
mechanism can replace this adapter without requiring provider history use cases
to learn its physical representation.

## Mission Event Serialization

`MissionEventSerializer` is the sole owner of the persisted JSON format for
canonical Mission events. `MissionJsonEventStore` delegates conversion between
typed `ExecutionEvent` objects and JSON to this serializer; repositories,
projection rebuilds, and verification work with typed events only. Provider
payloads, including `ProviderResolutionSnapshot`, are validated and serialized
through their typed Pydantic models. Changing the persisted event format only
requires changing the serializer while preserving the existing JSON schema.

```text
MissionJsonEventStore
        |
        v
MissionEventSerializer
        |
        v
Canonical Mission JSON
```

## Mission Event Schema Versioning

Each persisted Mission event has its own `schema_version`. New writes use the
current schema version, while historical events without that field are treated
as V0 and are lazily upcasted in memory before deserialization. No migration
rewrites canonical Mission JSON merely to add a schema version, so one Mission
stream may contain V0 and V1 events.

The current V1 envelope preserves the existing `sequence`, `timestamp`,
`type`, `message`, and `metadata` fields and adds only `schema_version: 1`.
Upcasters are pure, sequential, and must form a contiguous chain. A future
schema version fails explicitly instead of being deserialized as an older one.

The SQL repository currently replaces a Mission's full JSON event list during
an aggregate update. Therefore, saving a Mission loaded from V0 rewrites its
events as equivalent V1 representations; business fields, sequence, timestamp,
and payload stay unchanged. Incompatible persisted JSON changes require a new
current version, a contiguous upcaster, legacy fixtures, round-trip coverage,
and projection rebuild verification.

## Mission Event Identity

Every typed Mission event has an immutable `event_id` in addition to its
per-Mission `sequence`. New events receive UUIDv4 IDs during centralized
`Mission.record_event` creation. Persisted V2 events retain that ID, while V0
and V1 events are lazily upcasted to a deterministic UUIDv5 derived from the
stable namespace `a42492d0-825a-4c3d-908c-678e4900753b` and the exact name
`mission-event:v1:{mission_id}:{sequence}`. Thus repeated legacy loads produce
the same identity without rewriting canonical JSON. `event_id` is not exposed
by the public provider-history API and never replaces sequence-based cursors.
The existing provider-history projection keeps its established `(mission_id,
sequence)` identity and does not duplicate `event_id`; canonical Mission JSON
remains the source for event identity until a dedicated projection migration is
introduced.

## Mission Event Causality

Persisted V3 events add immutable `correlation_id` and optional `causation_id`.
The first event in a workflow is its own correlation root; each subsequent
event inherits that correlation and names the preceding event as its cause.
Legacy V0-V2 streams are upcasted deterministically in persisted order. These
fields remain internal metadata: they are not public API fields or cursors.

## Command Idempotency

Manual `run` and `confirm` commands require an `Idempotency-Key` header. The
key is reserved before provider work begins and the completed Mission result is
stored with it in PostgreSQL. Repeating the same key for the same Mission and
command returns that result without repeating provider side effects; using it
for another Mission or command returns a conflict. A command that raises before
completion releases its pending receipt, so the same key can be retried instead
of remaining permanently in progress. Scheduled processing and recovery do not
use this HTTP command boundary.

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
## Execution attempt audit

Every successful `claim_due` operation creates an immutable execution-attempt
record. The record is opened with the Mission claim and is closed when that
attempt completes, fails, waits for confirmation, or is recovered after a
stale claim. The audit record preserves the attempt number, claim time, final
outcome, and resolved provider when one was selected.

The database also enforces that a Mission has at most one open attempt. This
is a storage invariant in addition to the claim lock; completed, failed,
recovered, and confirmation-pending attempts remain available as history.

This history is diagnostic metadata only. It does not add a retry policy,
backoff, or automatic scheduling. Existing Missions are not backfilled with
invented attempt records; auditing starts with claims made after the migration.

Attempt records are available through the read-only API:

```bash
curl http://127.0.0.1:8000/missions/{mission_id}/execution-attempts
```

The response is ordered by attempt number and contains claim time, terminal
outcome when available, and the resolved provider. Reading it never executes
or changes a Mission.

## Mission event history

Canonical Mission events are also available as a bounded read-only sequence
page. This is useful for audit views and incremental UI refresh without loading
the full Mission response:

```bash
curl "http://127.0.0.1:8000/missions/{mission_id}/events?after_sequence=0&limit=50"
```

The endpoint returns only events whose sequence is strictly greater than
`after_sequence`, in ascending order. `latest_sequence` can be used as the next
position; `has_more` indicates that another bounded page is available. Reading
history never executes, schedules, resolves a provider, or changes the Mission.

Individual Mission responses include an `ETag` containing the current event
sequence. Mutating endpoints accept an optional `If-Match` value such as `"12"`.
When supplied, a stale version returns `409 mission_version_conflict`; this
allows clients to avoid applying an action based on an outdated Mission view.
Every successful Mission mutation also returns the resulting `ETag`, including
no-op updates and idempotent command replays, so clients can safely carry that
version into their next `If-Match` request without an extra read.

Clients that need a lightweight live audit view can use bounded long-polling on
the same endpoint:

```bash
curl "http://127.0.0.1:8000/missions/{mission_id}/events?after_sequence=42&wait_seconds=30"
```

The API reads immediately, then waits for newly committed canonical events for
at most 30 seconds. A timeout is still a successful `200` with an empty page
and `latest_sequence` equal to `after_sequence`. Each polling read opens and
releases its own database session before sleeping, so no transaction or pooled
connection is held during the wait. The batch remains bounded by `limit` and
uses the same strict sequence ordering as ordinary event-history reads.

PostgreSQL keeps a relational `mission_events` projection keyed by
`(mission_id, sequence)`. New events are appended in the same transaction as
their Mission update, and the history endpoint uses that projection for bounded
SQL reads. The existing JSON execution log remains the canonical compatible
event representation; the projection is a read model, not a second execution
engine.

The projection is also operationally verifiable and rebuildable. Administrators
can compare one Mission's canonical log with its relational rows without
changing either representation:

```text
GET /admin/missions/{mission_id}/event-projection/verification
```

The diagnostic reports counts plus missing, unexpected, or mismatched event
sequences. A mismatch returns a normal `200` response with status
`inconsistent`; it never repairs Mission data. Rebuild the full disposable
projection in one transaction when maintenance is required:

```bash
python -m app.cli rebuild-mission-events
```

Avoid normal Mission writes while the rebuild is running. The command replaces
only relational `mission_events` rows from canonical JSON; it does not execute,
schedule, recover, or otherwise mutate Missions.

## Provider reservation idempotency

Before reserving an option, Mission Engine supplies the selected provider with
a stable `idempotency_key` derived from the Mission ID. A provider must treat
repeated reserve calls with that key as the same logical reservation. This
protects the external reservation boundary when execution is retried after an
interrupted attempt; it does not add automatic retry or fallback behavior.

Every successful provider reservation must include a non-empty external
`reservation_id`. It is persisted on the Mission and on the completed execution
attempt as read-only execution metadata; Mission creation requests cannot set
it. The execution log also records a `reservation_succeeded` event with the
reservation ID and whether user confirmation is required.

Adapters report expected search and reservation failures with
`ProviderOperationError`. Mission Engine records a safe
`provider_operation_failed` event, moves the Mission to `failed`, and closes a
claimed execution attempt. Provider-supplied error text is not persisted in the
Mission audit log. Unexpected exceptions remain visible as infrastructure or
programming failures rather than being silently classified as provider errors.

## Provider reservation confirmation

When a reservation requires user confirmation, `POST /missions/{id}/confirm`
uses the provider that created the reservation, identified by
`resolved_provider_id`. The adapter receives the persisted `reservation_id` and
a stable confirmation idempotency key. The Mission records
`confirmation_started` before this provider operation and records either
`confirmation_succeeded` followed by completion, or a safe
`provider_operation_failed` event and `failed` status.

Legacy Mission records that predate reservation metadata remain confirmable with
the previous local-only transition. New reservation flows always use the
provider confirmation boundary.

## Mission cancellation

`POST /missions/{id}/cancel` performs one idempotent cancellation command and
requires an `Idempotency-Key` header. Missions in `created` or `waiting` move
directly to `cancelled`; no provider is contacted. A Mission awaiting user
confirmation first cancels its persisted reservation through the provider named
by `resolved_provider_id`, using a stable provider cancellation idempotency key,
then moves to `cancelled`.

Cancellation is intentionally unavailable while a Mission is `processing` or
in any terminal state. Provider cancellation events are retained in the audit
log. An expected provider cancellation failure is recorded safely and marks the
Mission `failed`, following the existing provider-operation failure policy.
