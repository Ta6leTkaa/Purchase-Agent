from dataclasses import dataclass
from datetime import datetime

from app.domain.task import AgentTask
from app.domain.task_intent import TaskIntent
from app.domain.task_permission import BrowserAction, PermissionDecision
from app.domain.task_plan import TaskPlan, TaskPlanStep
from app.services.task_intent_parser import extract_task_intent
from app.services.task_permission import evaluate_browser_action


@dataclass(frozen=True)
class TaskPlanPreview:
    plan: TaskPlan
    inferred_kind: str
    intent: TaskIntent
    decisions: tuple[PermissionDecision, ...]


def build_task_plan(task: AgentTask, now: datetime) -> TaskPlanPreview:
    """Build a conservative site-agnostic plan without storing personal values."""
    steps: list[TaskPlanStep] = [
        TaskPlanStep(
            step_id="open_target",
            action=BrowserAction.NAVIGATE,
            summary="Open the user-provided website",
            target_url=task.target_url,
        ),
        TaskPlanStep(
            step_id="inspect_page",
            action=BrowserAction.READ_PAGE,
            summary="Inspect the page and locate the relevant form",
            depends_on=("open_target",),
        ),
    ]
    previous = "inspect_page"
    kind = task.inferred_kind or infer_task_kind(task.instruction)
    intent = extract_task_intent(
        task.instruction,
        participant_count=len(task.person_ids),
        reference_date=now.date(),
    )
    if kind in _BASIC_PROFILE_KINDS:
        steps.append(
            TaskPlanStep(
                step_id="fill_people",
                action=BrowserAction.FILL_BASIC_PROFILE,
                summary="Fill basic details for the selected people",
                depends_on=(previous,),
                requested_fields=("first_name", "last_name", "birth_date"),
            )
        )
        previous = "fill_people"
    if kind in _DOCUMENT_KINDS:
        steps.append(
            TaskPlanStep(
                step_id="fill_documents",
                action=BrowserAction.FILL_SENSITIVE_PROFILE,
                summary="Fill required identity document details after approval",
                depends_on=(previous,),
                requested_fields=("document_type", "document_number"),
            )
        )
        previous = "fill_documents"
    steps.extend(
        [
            TaskPlanStep(
                step_id="choose_option",
                action=BrowserAction.SELECT_OPTION,
                summary="Choose the option that best matches the request",
                depends_on=(previous,),
            ),
            TaskPlanStep(
                step_id="open_review",
                action=BrowserAction.PREPARE_REVIEW,
                summary="Advance to the review page without confirming the order",
                depends_on=("choose_option",),
            ),
            TaskPlanStep(
                step_id="prepare_order",
                action=BrowserAction.SUBMIT_ORDER,
                summary="Prepare the order and stop before irreversible submission",
                depends_on=("open_review",),
                reversible=False,
            ),
        ]
    )
    plan = TaskPlan(
        task_id=task.id,
        version=(task.plan.version + 1 if task.plan is not None else 1),
        created_at=now,
        steps=tuple(steps),
    )
    return TaskPlanPreview(
        plan=plan,
        inferred_kind=kind,
        intent=intent,
        decisions=tuple(
            evaluate_browser_action(task, step.to_action_request())
            for step in plan.steps
        ),
    )


def infer_task_kind(instruction: str) -> str:
    normalized = instruction.casefold()
    for kind, markers in _KIND_MARKERS:
        if any(marker in normalized for marker in markers):
            return kind
    return "generic_purchase"


_KIND_MARKERS = (
    ("train_ticket", ("поезд", "жд", "ржд", "train")),
    ("flight_ticket", ("авиа", "самол", "flight")),
    ("hotel", ("отел", "гостиниц", "hotel")),
    ("theatre_ticket", ("театр", "спектак", "theatre", "theater")),
    ("cinema_ticket", ("кино", "сеанс", "cinema", "movie")),
)
_BASIC_PROFILE_KINDS = {"train_ticket", "flight_ticket", "hotel"}
_DOCUMENT_KINDS = {"train_ticket", "flight_ticket"}
