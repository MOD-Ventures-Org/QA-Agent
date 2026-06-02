import json
import socket
from dataclasses import dataclass, field
from typing import List, Optional

from claude.client import DualAIClient
from claude.repo_context import RepoContext, detect_repo_type

from config import settings
from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from claude.prompts import ANALYZER_SYSTEM, analyzer_user_prompt

logger = get_logger(__name__)
client = DualAIClient(
    settings.anthropic_api_key,
    settings.kimi_api_key,
    settings.kimi_model,
    settings.kimi_api_url,
)


@dataclass
class TestPlan:
    reasoning: str = ""
    should_test: bool = False        # is there anything worth generating + running tests for?
    test_kind: str = "mixed"         # "ui" | "api" | "functional" | "mixed" — drives what gets generated
    pytest_keyword: str = ""         # optional pytest -k narrowing for the generated tests
    priority: str = "medium"
    focus_areas: List[str] = field(default_factory=list)
    affected_pages: List[str] = field(default_factory=list)


DEPLOYMENT_EVENTS = ("deployment", "deployment_status")


def _test_kind_for_repo(repo_type: str) -> str:
    if repo_type == "frontend":
        return "ui"
    if repo_type == "backend":
        return "api"
    return "mixed"


def _deployment_validation_plan(repo_type: str) -> TestPlan:
    return TestPlan(
        reasoning="Deployment event — generate smoke validation from README/base URL",
        should_test=True,
        test_kind=_test_kind_for_repo(repo_type),
        priority="high",
    )


def _testable_fallback_plan(reasoning: str, repo_type: str) -> TestPlan:
    """Used when Claude can't produce a plan but the AI is reachable: assume the
    change is testable and let the generator decide what to write."""
    return TestPlan(
        reasoning=reasoning,
        should_test=True,
        test_kind=_test_kind_for_repo(repo_type),
        priority="medium",
    )


def _is_deployment_event(event: GitHubPushEvent) -> bool:
    return event.event_type in DEPLOYMENT_EVENTS


def _is_network_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(term in text for term in ("getaddrinfo", "connection", "timed out", "timeout", "network", "resolve", "11001"))


AI_HEALTHCHECK_HOST = "api.anthropic.com"


def ai_reachable(host: str = AI_HEALTHCHECK_HOST) -> bool:
    """Cheap DNS check that the AI provider host can be resolved. Catches the
    offline / getaddrinfo case before we do any pipeline work."""
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


async def analyze_event(event: GitHubPushEvent, repo_context: Optional[RepoContext] = None) -> TestPlan:
    repo_type = repo_context.repo_type if repo_context else detect_repo_type(event.repo_name, event.changed_files)
    logger.info(
        f"Analyzing event type={event.event_type} repo={event.repo_name} "
        f"branch={event.branch} repo_type={repo_type}"
    )

    if _is_deployment_event(event):
        logger.info("Deployment event — generating smoke validation tests")
        return _deployment_validation_plan(repo_type)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            temperature=0,
            system=ANALYZER_SYSTEM,
            messages=[{"role": "user", "content": analyzer_user_prompt(event, repo_context, repo_type)}],
        )
        raw = message.content[0].text.strip()
        data = json.loads(raw)
        plan = TestPlan(**{k: v for k, v in data.items() if k in TestPlan.__dataclass_fields__})
        # If the model left test_kind blank, infer it from the repo type.
        if not plan.test_kind:
            plan.test_kind = _test_kind_for_repo(repo_type)
        logger.info(
            f"Analyzer decided should_test={plan.should_test} test_kind={plan.test_kind} "
            f"keyword={plan.pytest_keyword!r} priority={plan.priority} reasoning={plan.reasoning[:80]}"
        )
        return plan
    except Exception as e:
        logger.error(f"Claude analyzer failed: {e} — assuming change is testable")
        reason = "AI analysis unavailable — generated tests from the change itself."
        if _is_network_error(e):
            reason = "Could not reach the AI service (network) — generated tests from the change itself."
        return _testable_fallback_plan(reason, repo_type)
