from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel

from app.services.deployment_smoke import validate_deployment_base_url


class DeploymentFlowResult(BaseModel):
    ok: bool
    stage: str
    mission_id: UUID | None = None
    final_status: str | None = None
    message: str


class _FlowFailure(Exception):
    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        self.message = message
        super().__init__(message)


async def run_deployment_flow(
    *,
    base_url: str,
    api_key: str,
    provider_id: str = "mock_train",
    timeout_seconds: float = 15,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DeploymentFlowResult:
    normalized_base_url = validate_deployment_base_url(base_url)
    normalized_provider_id = provider_id.strip()
    if not normalized_provider_id:
        raise ValueError("provider_id must not be empty")
    run_id = uuid4().hex
    headers = {"X-API-Key": api_key}
    mission_id: UUID | None = None
    try:
        async with httpx.AsyncClient(
            base_url=normalized_base_url,
            timeout=timeout_seconds,
            transport=transport,
        ) as client:
            identity = await _post_json(
                client,
                "/identities",
                stage="create_identity",
                headers={**headers, "Idempotency-Key": f"flow-{run_id}-identity"},
                json={
                    "display_name": "Deployment Flow Passenger",
                    "first_name": "Deployment",
                    "last_name": "Flow",
                    "birth_date": "1990-01-01",
                    "documents": [],
                },
            )
            identity_id = _uuid_field(identity, "id", "create_identity")
            travel_date = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()
            mission = await _post_json(
                client,
                "/missions",
                stage="create_mission",
                headers={**headers, "Idempotency-Key": f"flow-{run_id}-mission"},
                json={
                    "type": "train_trip",
                    "title": "Deployment flow validation",
                    "participant_ids": [str(identity_id)],
                    "provider": normalized_provider_id,
                    "provider_id": normalized_provider_id,
                    "execution_mode": "require_confirmation",
                    "payload": {
                        "origin": "Moscow",
                        "destination": "Saint Petersburg",
                        "departure_date": travel_date,
                    },
                    "constraints": {
                        "from_city": "Moscow",
                        "to_city": "Saint Petersburg",
                        "travel_date": travel_date,
                        "passengers_count": 1,
                    },
                },
            )
            mission_id = _uuid_field(mission, "id", "create_mission")
            run_response = await _post_json(
                client,
                f"/missions/{mission_id}/run",
                stage="run_mission",
                headers={**headers, "Idempotency-Key": f"flow-{run_id}-run"},
            )
            if run_response.get("status") != "requires_confirmation":
                raise _FlowFailure(
                    "run_mission",
                    "Mission did not reach confirmation state.",
                )
            confirmation = await _post_json(
                client,
                f"/missions/{mission_id}/confirm",
                stage="confirm_mission",
                headers={**headers, "Idempotency-Key": f"flow-{run_id}-confirm"},
            )
            if confirmation.get("status") != "completed":
                raise _FlowFailure(
                    "confirm_mission",
                    "Mission did not complete after confirmation.",
                )
            outcome = await _get_json(
                client,
                f"/missions/{mission_id}/outcome",
                stage="verify_outcome",
                headers=headers,
            )
            if not (
                outcome.get("status") == "completed"
                and outcome.get("terminal") is True
                and outcome.get("successful") is True
                and outcome.get("next_action") == "none"
            ):
                raise _FlowFailure(
                    "verify_outcome",
                    "Mission outcome is not a successful terminal result.",
                )
    except _FlowFailure as exc:
        return DeploymentFlowResult(
            ok=False,
            stage=exc.stage,
            mission_id=mission_id,
            message=exc.message,
        )
    except httpx.HTTPError:
        return DeploymentFlowResult(
            ok=False,
            stage="request",
            mission_id=mission_id,
            message="API request failed.",
        )
    return DeploymentFlowResult(
        ok=True,
        stage="complete",
        mission_id=mission_id,
        final_status="completed",
        message="Deployment flow completed successfully.",
    )


async def _post_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    stage: str,
    headers: dict[str, str],
    json: dict[str, object] | None = None,
) -> dict[str, object]:
    response = await client.post(path, headers=headers, json=json)
    return _response_json(response, stage)


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    stage: str,
    headers: dict[str, str],
) -> dict[str, object]:
    response = await client.get(path, headers=headers)
    return _response_json(response, stage)


def _response_json(response: httpx.Response, stage: str) -> dict[str, object]:
    if response.status_code != 200:
        raise _FlowFailure(stage, f"API returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise _FlowFailure(stage, "API returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise _FlowFailure(stage, "API returned an unexpected response shape.")
    return payload


def _uuid_field(payload: dict[str, object], name: str, stage: str) -> UUID:
    try:
        return UUID(str(payload[name]))
    except (KeyError, ValueError) as exc:
        raise _FlowFailure(stage, f"API response has no valid {name}.") from exc
