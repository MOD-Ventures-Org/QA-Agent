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
    run_load_tests: bool = False
    pytest_keyword: str = ""
    priority: str = "medium"
    focus_areas: List[str] = field(default_factory=list)
    affected_pages: List[str] = field(default_factory=list)


DEPLOYMENT_EVENTS = ("deployment", "deployment_status")

# Repo-type guardrails: a backend repo never runs UI suites; a frontend repo
# never runs API/load suites. Functional + generated suites are allowed for both.
FRONTEND_ONLY_FLAGS = ("run_ui_smoke", "run_ui_regression", "run_ui_critical_paths", "run_accessibility")
BACKEND_ONLY_FLAGS = ("run_api_endpoints", "run_api_auth", "run_api_contracts", "run_load_tests")


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
        run_load_tests=True,
        priority="high",
    )


def _smoke_plan(reasoning: str = "Smoke fallback — minimal sanity suites") -> TestPlan:
    """Minimal safety net used when Claude can't produce a plan. Guardrails trim
    this to api_endpoints for backend repos and ui_smoke for frontend repos."""
    return TestPlan(
        reasoning=reasoning,
        run_ui_smoke=True,
        run_api_endpoints=True,
        priority="medium",
    )


def _deployment_validation_plan(reasoning: str = "Deployment event — validate that the current changes are successfully deployed") -> TestPlan:
    return _all_suites_plan(reasoning)


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


def _apply_repo_guardrail(plan: TestPlan, repo_type: str) -> TestPlan:
    """Force-disable suites that don't belong to the repo type."""
    disabled = ()
    if repo_type == "backend":
        disabled = FRONTEND_ONLY_FLAGS
    elif repo_type == "frontend":
        disabled = BACKEND_ONLY_FLAGS
    for flag in disabled:
        if getattr(plan, flag, False):
            setattr(plan, flag, False)
    if disabled:
        plan.reasoning = f"{plan.reasoning} [guardrail: {repo_type} repo — disabled {', '.join(disabled)}]"
    return plan


async def analyze_event(event: GitHubPushEvent, repo_context: Optional[RepoContext] = None) -> TestPlan:
    repo_type = repo_context.repo_type if repo_context else detect_repo_type(event.repo_name, event.changed_files)
    logger.info(
        f"Analyzing event type={event.event_type} repo={event.repo_name} "
        f"branch={event.branch} repo_type={repo_type}"
    )

    if _is_deployment_event(event):
        logger.info("Deployment event — running full validation (guardrail-trimmed)")
        return _apply_repo_guardrail(_deployment_validation_plan(), repo_type)

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
        plan = _apply_repo_guardrail(plan, repo_type)
        logger.info(
            f"Analyzer decided priority={plan.priority} keyword={plan.pytest_keyword!r} "
            f"reasoning={plan.reasoning[:80]}"
        )
        return plan
    except Exception as e:
        logger.error(f"Claude analyzer failed: {e} — falling back to smoke suites")
        reason = "AI analysis unavailable — ran a minimal smoke check based on repo type."
        if _is_network_error(e):
            reason = "Could not reach the AI service (network) — ran a minimal smoke check."
        return _apply_repo_guardrail(_smoke_plan(reason), repo_type)
