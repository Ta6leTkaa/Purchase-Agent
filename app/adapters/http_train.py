from collections.abc import Mapping
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.adapters.base import ProviderAdapter
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionType
from app.domain.provider import (
    CancellationResult,
    ConfirmationResult,
    ProviderOption,
    ReservationResult,
)
from app.domain.provider_capability import ProviderCapability
from app.services.provider_errors import ProviderOperationError


class _SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options: list[ProviderOption]


class HttpTrainAdapter(ProviderAdapter):
    """Adapter for a train inventory gateway implementing the MVP contract."""

    PROVIDER_ID: Final = "http_train"
    _CAPABILITIES = frozenset(
        {ProviderCapability(mission_type=MissionType.TRAIN_TICKET)}
    )

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/")
        if not normalized_url:
            raise ValueError("base_url must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._base_url = normalized_url + "/"
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @property
    def provider_id(self) -> str:
        return self.PROVIDER_ID

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return self._CAPABILITIES

    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        payload = mission.payload
        assert payload is not None
        response = await self._request(
            "POST",
            "v1/train/options/search",
            operation="search",
            json={
                "mission_id": str(mission.id),
                "origin": payload.origin,
                "destination": payload.destination,
                "departure_date": payload.departure_date.isoformat(),
                "passengers": [
                    identity.model_dump(mode="json") for identity in identities
                ],
                "constraints": mission.constraints.model_dump(mode="json"),
            },
        )
        try:
            return _SearchResponse.model_validate(response).options
        except ValidationError as exc:
            raise self._failure("search", retryable=True) from exc

    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
        *,
        idempotency_key: str,
    ) -> ReservationResult:
        response = await self._request(
            "POST",
            "v1/train/reservations",
            operation="reservation",
            idempotency_key=idempotency_key,
            json={
                "mission_id": str(mission.id),
                "option": option.model_dump(mode="json"),
            },
        )
        return self._validate_result(
            ReservationResult,
            response,
            operation="reservation",
        )

    async def confirm_reservation(
        self,
        reservation_id: str,
        mission: Mission,
        *,
        idempotency_key: str,
    ) -> ConfirmationResult:
        response = await self._request(
            "POST",
            f"v1/train/reservations/{reservation_id}/confirm",
            operation="confirmation",
            idempotency_key=idempotency_key,
            json={"mission_id": str(mission.id)},
        )
        return self._validate_result(
            ConfirmationResult,
            response,
            operation="confirmation",
        )

    async def cancel_reservation(
        self,
        reservation_id: str,
        mission: Mission,
        *,
        idempotency_key: str,
    ) -> CancellationResult:
        response = await self._request(
            "POST",
            f"v1/train/reservations/{reservation_id}/cancel",
            operation="cancellation",
            idempotency_key=idempotency_key,
            json={"mission_id": str(mission.id)},
        )
        return self._validate_result(
            CancellationResult,
            response,
            operation="cancellation",
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> object:
        headers = {"Accept": "application/json"}
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    headers=headers,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise self._failure(operation, retryable=True) from exc
        if response.status_code >= 400:
            raise self._failure(
                operation,
                retryable=(
                    response.status_code in {408, 425, 429}
                    or response.status_code >= 500
                ),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise self._failure(operation, retryable=True) from exc

    def _validate_result[
        ResultT: BaseModel
    ](
        self,
        result_type: type[ResultT],
        payload: object,
        *,
        operation: str,
    ) -> ResultT:
        try:
            return result_type.model_validate(payload)
        except ValidationError as exc:
            raise self._failure(operation, retryable=True) from exc

    def _failure(self, operation: str, *, retryable: bool) -> ProviderOperationError:
        return ProviderOperationError(
            provider_id=self.provider_id,
            operation=operation,
            retryable=retryable,
        )
