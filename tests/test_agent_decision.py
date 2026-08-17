import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.openai_agent import (
    AgentDecisionProviderError,
    OpenAIAgentDecisionProvider,
)
from app.domain.browser_command import ClickCommand
from app.domain.browser_page import (
    BrowserControlKind,
    BrowserPageControl,
    BrowserPageSnapshot,
)
from app.domain.task import AgentTask
from app.domain.task_intent import TaskIntent
from app.services.agent_decision import build_agent_decision_context

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def _task() -> AgentTask:
    return AgentTask(
        id=uuid4(),
        instruction="Купи билет на Колобка завтра, любое время",
        target_url="https://cinema.example.com/film/kolobok",
        person_ids=(uuid4(),),
        intent=TaskIntent(
            requested_date=NOW.date(),
            participant_count=1,
            search_terms=("Колобок",),
        ),
        page_snapshot=BrowserPageSnapshot(
            url="https://cinema.example.com/film/kolobok",
            title="Последний богатырь. Колобок",
            captured_at=NOW,
            controls=(
                BrowserPageControl(
                    control_id="control_1",
                    kind=BrowserControlKind.CLICKABLE,
                    label="Завтра",
                    role="tab",
                ),
                BrowserPageControl(
                    control_id="control_2",
                    kind=BrowserControlKind.TEXT,
                    label="Имя",
                    field_name="first_name",
                ),
            ),
        ),
        created_at=NOW,
    )


def test_context_contains_goal_and_controls_but_no_profile_values() -> None:
    context = build_agent_decision_context(_task())
    serialized = context.model_dump_json()

    assert context.goal.startswith("Купи билет")
    assert context.controls[0].label == "Завтра"
    assert context.intent["search_terms"] == ["Колобок"]
    assert "first_name" not in serialized
    assert "document_number" not in serialized


def test_context_requires_observed_page() -> None:
    task = _task().model_copy(update={"page_snapshot": None})

    with pytest.raises(ValueError, match="page snapshot"):
        build_agent_decision_context(task)


@pytest.mark.asyncio
async def test_openai_provider_requests_strict_structured_decision() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "command": {
                                            "action": "click",
                                            "control_id": "control_1",
                                        },
                                        "rationale": "Select requested date",
                                        "expected_result": "Sessions update",
                                    }
                                ),
                            }
                        ],
                    }
                ]
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.openai.com/v1",
    )
    provider = OpenAIAgentDecisionProvider(
        api_key=SecretStr("test-openai-key"),
        model="test-model",
        client=client,
    )

    decision = await provider.decide(build_agent_decision_context(_task()))

    assert isinstance(decision.command, ClickCommand)
    assert captured["authorization"] == "Bearer test-openai-key"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "test-model"
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert "test-openai-key" not in json.dumps(payload)
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_provider_rejects_unstructured_response() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"output": []})
        ),
        base_url="https://api.openai.com/v1",
    )
    provider = OpenAIAgentDecisionProvider(
        api_key=SecretStr("test-openai-key"),
        model="test-model",
        client=client,
    )

    with pytest.raises(AgentDecisionProviderError, match="no structured"):
        await provider.decide(build_agent_decision_context(_task()))
    await client.aclose()
