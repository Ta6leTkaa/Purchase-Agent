import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.ollama_agent import OllamaAgentDecisionProvider
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
from app.domain.task import AgentTask, TaskClarification
from app.domain.task_intent import TaskIntent
from app.services.agent_decision import (
    AgentPageStage,
    build_agent_decision_context,
    classify_agent_page_stage,
)

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
            visible_text="Последний богатырь. Колобок\nСегодня Завтра\n18:00 19:15",
            captured_at=NOW,
            controls=(
                BrowserPageControl(
                    control_id="control_1",
                    frame_index=1,
                    frame_url="https://cinema.example.com/widget/schedule",
                    kind=BrowserControlKind.CLICKABLE,
                    label="Завтра",
                    role="tab",
                    nearby_text="Сегодня Завтра Вторник, 18 августа",
                    selected=True,
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
    assert context.controls[0].frame_index == 1
    assert context.controls[0].frame_url.endswith("/widget/schedule")
    assert "18 августа" in (context.controls[0].nearby_text or "")
    assert context.controls[0].selected is True
    assert context.intent["search_terms"] == ["Колобок"]
    assert context.page_stage is AgentPageStage.FORM_ENTRY
    assert context.controls[0].field_name is None
    assert context.controls[1].field_name == "first_name"
    assert context.controls[0].goal_match_score >= 0.9
    assert "18:00" in context.visible_text
    assert "first_name" in serialized
    assert "document_number" not in serialized


@pytest.mark.parametrize(
    ("kind", "label", "expected"),
    [
        (BrowserControlKind.TEXT, "Password", AgentPageStage.AUTHENTICATION),
        (BrowserControlKind.BUTTON, "Оплатить", AgentPageStage.REVIEW),
        (BrowserControlKind.CANVAS, "Схема мест", AgentPageStage.VISUAL_SELECTION),
        (BrowserControlKind.DATE, "Дата", AgentPageStage.FORM_ENTRY),
        (BrowserControlKind.SELECT, "Время", AgentPageStage.OPTION_SELECTION),
        (BrowserControlKind.LINK, "Результат", AgentPageStage.BROWSING),
        (BrowserControlKind.OTHER, "Информация", AgentPageStage.UNKNOWN),
    ],
)
def test_page_stage_uses_generic_interaction_signals(
    kind: BrowserControlKind,
    label: str,
    expected: AgentPageStage,
) -> None:
    snapshot = BrowserPageSnapshot(
        url="https://example.com/flow",
        title="Flow",
        captured_at=NOW,
        controls=(
            BrowserPageControl(
                control_id="control_1",
                kind=kind,
                label=label,
            ),
        ),
    )

    assert classify_agent_page_stage(snapshot) is expected


def test_context_scores_partial_goal_match_in_control_card() -> None:
    task = _task()
    snapshot = BrowserPageSnapshot(
        url=task.target_url,
        title="Афиша",
        captured_at=NOW,
        controls=(
            BrowserPageControl(
                control_id="control_1",
                kind=BrowserControlKind.LINK,
                label="Подробнее",
                nearby_text="Последний богатырь. Колобок",
            ),
        ),
    )

    context = build_agent_decision_context(
        task.model_copy(update={"page_snapshot": snapshot})
    )

    assert context.controls[0].goal_match_score == 0.94


def test_context_keeps_relevant_late_controls_and_drops_visual_noise() -> None:
    task = _task()
    controls = tuple(
        BrowserPageControl(
            control_id=f"control_{index}",
            kind=BrowserControlKind.LINK,
            label=f"Служебная ссылка {index}",
        )
        for index in range(1, 81)
    ) + (
        BrowserPageControl(
            control_id="control_81",
            kind=BrowserControlKind.SVG,
            label="Visual SVG",
        ),
        BrowserPageControl(
            control_id="control_82",
            kind=BrowserControlKind.BUTTON,
            label="Колобок — выбрать сеанс",
        ),
    )
    snapshot = BrowserPageSnapshot(
        url=task.target_url,
        title="Афиша",
        captured_at=NOW,
        controls=controls,
    )

    context = build_agent_decision_context(
        task.model_copy(update={"page_snapshot": snapshot})
    )

    assert len(context.controls) == 60
    assert context.total_control_count == 82
    assert context.controls_truncated is True
    assert any(control.control_id == "control_82" for control in context.controls)
    assert all(control.control_id != "control_81" for control in context.controls)


def test_context_deduplicates_equivalent_controls() -> None:
    task = _task()
    snapshot = BrowserPageSnapshot(
        url=task.target_url,
        title="Афиша",
        captured_at=NOW,
        controls=(
            BrowserPageControl(
                control_id="control_1",
                kind=BrowserControlKind.LINK,
                label="Кинотеатры",
            ),
            BrowserPageControl(
                control_id="control_2",
                kind=BrowserControlKind.CLICKABLE,
                label="Кинотеатры",
            ),
        ),
    )

    context = build_agent_decision_context(
        task.model_copy(update={"page_snapshot": snapshot})
    )

    assert [control.control_id for control in context.controls] == ["control_1"]


def test_context_requires_observed_page() -> None:
    task = _task().model_copy(update={"page_snapshot": None})

    with pytest.raises(ValueError, match="page snapshot"):
        build_agent_decision_context(task)


def test_context_includes_user_clarifications() -> None:
    task = _task().model_copy(
        update={
            "clarifications": (
                TaskClarification(
                    question="Какой город?",
                    answer="Тверь",
                    created_at=NOW,
                ),
            )
        }
    )

    context = build_agent_decision_context(task)

    assert context.clarifications[0].question == "Какой город?"
    assert context.clarifications[0].answer == "Тверь"


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
    assert payload["input"][0]["content"][0]["type"] == "input_text"
    assert '"page_stage":"form_entry"' in payload["input"][0]["content"][0]["text"]
    assert "test-openai-key" not in json.dumps(payload)
    await client.aclose()


@pytest.mark.asyncio
async def test_ollama_provider_requests_local_structured_visual_decision() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "command": {
                                "action": "click",
                                "control_id": "control_1",
                            },
                            "rationale": "Select requested date",
                            "expected_result": "Sessions update",
                        }
                    )
                }
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://localhost:11434",
    )
    provider = OllamaAgentDecisionProvider(
        base_url="http://localhost:11434",
        model="qwen3-vl:4b",
        client=client,
    )
    context = build_agent_decision_context(_task()).model_copy(
        update={"screenshot_data_url": "data:image/jpeg;base64,aW1hZ2U="}
    )

    decision = await provider.decide(context)

    assert isinstance(decision.command, ClickCommand)
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen3-vl:4b"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"]["num_ctx"] == 32768
    assert payload["format"]["type"] == "object"
    assert payload["messages"][1]["images"] == ["aW1hZ2U="]
    assert "screenshot_data_url" not in payload["messages"][1]["content"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ollama_provider_accepts_structured_output_in_thinking_field() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "thinking": json.dumps(
                        {
                            "command": {
                                "action": "click",
                                "control_id": "control_1",
                            },
                            "rationale": "Continue the task",
                            "expected_result": "The next page opens",
                        }
                    ),
                }
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://localhost:11434",
    )
    provider = OllamaAgentDecisionProvider(
        base_url="http://localhost:11434",
        model="qwen3-vl:4b",
        client=client,
    )

    decision = await provider.decide(build_agent_decision_context(_task()))

    assert isinstance(decision.command, ClickCommand)
    await client.aclose()


@pytest.mark.asyncio
async def test_ollama_provider_retries_invalid_fast_model_on_primary() -> None:
    requested_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        if payload["model"] == "qwen3-vl:2b":
            return httpx.Response(200, json={"message": {"content": "{}"}})
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "command": {
                                "action": "click",
                                "control_id": "control_1",
                            },
                            "rationale": "Use the requested date",
                            "expected_result": "Schedule updates",
                        }
                    )
                }
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://localhost:11434",
    )
    provider = OllamaAgentDecisionProvider(
        base_url="http://localhost:11434",
        model="qwen3-vl:4b",
        fast_model="qwen3-vl:2b",
        client=client,
    )

    decision = await provider.decide(build_agent_decision_context(_task()))

    assert isinstance(decision.command, ClickCommand)
    assert requested_models == ["qwen3-vl:2b", "qwen3-vl:4b"]
    await client.aclose()


@pytest.mark.asyncio
async def test_ollama_provider_uses_primary_model_for_review_page() -> None:
    requested_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "command": {
                                "action": "click",
                                "control_id": "control_1",
                            },
                            "rationale": "Inspect the review page",
                            "expected_result": "Review remains safe",
                        }
                    )
                }
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://localhost:11434",
    )
    provider = OllamaAgentDecisionProvider(
        base_url="http://localhost:11434",
        model="qwen3-vl:4b",
        fast_model="qwen3-vl:2b",
        client=client,
    )
    context = build_agent_decision_context(_task()).model_copy(
        update={"page_stage": AgentPageStage.REVIEW}
    )

    await provider.decide(context)

    assert requested_models == ["qwen3-vl:4b"]
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_provider_sends_transient_visual_context() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
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
                                        "rationale": "Select visual option",
                                        "expected_result": "Page advances",
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
    context = build_agent_decision_context(
        _task(), screenshot_data_url="data:image/jpeg;base64,dGVzdA=="
    )

    await provider.decide(context)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    content = payload["input"][0]["content"]
    assert content[1] == {
        "type": "input_image",
        "image_url": "data:image/jpeg;base64,dGVzdA==",
        "detail": "low",
    }
    assert "screenshot_data_url" not in content[0]["text"]
    assert "dGVzdA" not in content[0]["text"]
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
