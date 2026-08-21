import base64
import json

import httpx
from pydantic import ValidationError

from app.adapters.openai_agent import _AGENT_INSTRUCTIONS, AgentDecisionProviderError
from app.domain.browser_command import AgentDecision
from app.services.agent_decision import AgentDecisionContext, AgentPageStage


class OllamaAgentDecisionProvider:
    """Local multimodal decision provider using Ollama's structured output API."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        fast_model: str | None = None,
        context_window: int = 32768,
        timeout_seconds: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._fast_model = fast_model
        self._context_window = context_window
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
        )

    async def __aenter__(self) -> "OllamaAgentDecisionProvider":
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def decide(self, context: AgentDecisionContext) -> AgentDecision:
        if self._should_use_fast_model(context):
            try:
                return await self._request_decision(
                    context, self._fast_model or self._model
                )
            except AgentDecisionProviderError:
                pass
        return await self._request_decision(context, self._model)

    def _should_use_fast_model(self, context: AgentDecisionContext) -> bool:
        if self._fast_model is None or self._fast_model == self._model:
            return False
        if context.page_stage in {
            AgentPageStage.AUTHENTICATION,
            AgentPageStage.REVIEW,
            AgentPageStage.VISUAL_SELECTION,
        }:
            return False
        if (
            context.previous_actions
            and context.previous_actions[-1].page_changed is False
        ):
            return False
        return True

    async def _request_decision(
        self,
        context: AgentDecisionContext,
        model: str,
    ) -> AgentDecision:
        message: dict[str, object] = {
            "role": "user",
            "content": json.dumps(
                context.model_dump(mode="json", exclude={"screenshot_data_url"}),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        if context.screenshot_data_url is not None:
            message["images"] = [_image_base64(context.screenshot_data_url)]
        try:
            response = await self._client.post(
                "/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "think": False,
                    "format": AgentDecision.model_json_schema(),
                    "messages": [
                        {"role": "system", "content": _AGENT_INSTRUCTIONS},
                        message,
                    ],
                    "options": {
                        "temperature": 0,
                        "num_ctx": self._context_window,
                    },
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise AgentDecisionProviderError(
                f"Ollama request failed with {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentDecisionProviderError("Ollama request failed") from exc
        payload = response.json()
        response_message = payload.get("message", {})
        content = response_message.get("content")
        if not content:
            content = response_message.get("thinking")
        if not isinstance(content, str):
            raise AgentDecisionProviderError("Ollama response has no message content")
        try:
            return AgentDecision.model_validate_json(content)
        except ValidationError as exc:
            raise AgentDecisionProviderError(
                "Ollama response did not match the browser decision schema: "
                f"{exc.error_count()} validation error(s)"
            ) from exc


def _image_base64(data_url: str) -> str:
    prefix, separator, encoded = data_url.partition(",")
    if separator != "," or ";base64" not in prefix:
        raise AgentDecisionProviderError("Screenshot is not a base64 data URL")
    try:
        base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise AgentDecisionProviderError("Screenshot contains invalid base64") from exc
    return encoded
