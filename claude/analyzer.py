import json
from dataclasses import dataclass, field
from typing import List

from claude.client import DualAIClient

from config import settings
from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from claude.prompts import ANALYZER_SYSTEM, analyzer_user_prompt

logger = get_logger(__name__)
client = DualAIClient(settings.anthropic_api_key, settings.gemini_api_key)


@dataclass
class TestPlan:
    reasoning: str = ""
    run_ui_smoke: bool = False
    run_ui_regression: bool = False
    run_ui_critical_paths: bool = False
    run_api_endpoints: bool = False
    run_api_auth: bool = False
    run_api_contracts: bool = False
    run_functional_integration: bool = False
    run_functional_edge_cases: bool = False
    run_accessibility: bool = False
    run_generated_tests: bool = False
    priority: str = "medium"
    focus_areas: List[str] = field(default_factory=list)
    affected_pages: List[str] = field(default_factory=list)


DEPLOYMENT_EVENTS = ("deployment", "deployment_status")
BACKEND_REPO_PATTERNS = ("backend", "/backend", "api", "server")
FRONTEND_REPO_PATTERNS = ("frontend", "/frontend", "web", "client", "ui")


def _all_suites_plan(reasoning: str = "Fallback: running all suites") -> TestPlan:
    return TestPlan(
        reasoning=reasoning,
        run_ui_smoke=True,
        run_ui_regression=True,
        run_ui_critical_paths=True,
        run_api_endpoints=True,
        run_api_auth=True,
        run_api_contracts=True,
        run_functional_integration=True,
        run_functional_edge_cases=True,
        run_accessibility=True,
        run_generated_tests=True,
        priority="high",
    )


def _backend_api_plan(reasoning: str = "Backend repo event — run API testing only") -> TestPlan:
    return TestPlan(
        reasoning=reasoning,
        run_api_endpoints=True,
        run_api_auth=True,
        run_api_contracts=True,
        priority="high",
    )


def _frontend_ui_integration_plan(reasoning: str = "Frontend repo event — run UI, integration, and functional testing") -> TestPlan:
    return TestPlan(
        reasoning=reasoning,
        run_ui_smoke=True,
        run_ui_regression=True,
        run_ui_critical_paths=True,
        run_functional_integration=True,
        run_functional_edge_cases=True,
        run_accessibility=True,
        priority="high",
    )


def _deployment_validation_plan(reasoning: str = "Deployment event — validate that the current changes are successfully deployed") -> TestPlan:
    return _all_suites_plan(reasoning)


def _is_backend_repo(repo_name: str) -> bool:
    lower = repo_name.lower()
    return any(pattern in lower for pattern in BACKEND_REPO_PATTERNS)


def _is_frontend_repo(repo_name: str) -> bool:
    lower = repo_name.lower()
    return any(pattern in lower for pattern in FRONTEND_REPO_PATTERNS)


def _is_deployment_event(event: GitHubPushEvent) -> bool:
    return event.event_type in DEPLOYMENT_EVENTS


async def analyze_event(event: GitHubPushEvent) -> TestPlan:
    if _is_deployment_event(event):
        logger.info("Deployment event detected — enforcing deployment validation plan")
        return _deployment_validation_plan()

    if _is_backend_repo(event.repo_name):
        logger.info("Backend repo event detected — enforcing API-only test plan")
        return _backend_api_plan()

    if _is_frontend_repo(event.repo_name):
        logger.info("Frontend repo event detected — enforcing UI/integration + functional test plan")
        return _frontend_ui_integration_plan()

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            temperature=0,
            system=ANALYZER_SYSTEM,
            messages=[{"role": "user", "content": analyzer_user_prompt(event)}],
        )
        raw = message.content[0].text.strip()
        data = json.loads(raw)
        return TestPlan(**{k: v for k, v in data.items() if k in TestPlan.__dataclass_fields__})
    except Exception as e:
        logger.error(f"Claude analyzer failed: {e} — falling back to all suites")
        return _all_suites_plan(f"Parse error: {e}")
