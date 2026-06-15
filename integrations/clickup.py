from typing import List

import httpx

from config import settings
from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from claude.analyzer import TestPlan
from claude.report_writer import BugReport, plain_title
from testing.result_parser import TestResult

logger = get_logger(__name__)

PRIORITY_MAP = {"critical": 1, "high": 2, "medium": 3, "low": 4}
CLICKUP_API_BASE = "https://api.clickup.com/api/v2"


def _bug_items_markdown(result: TestResult, bug_report: BugReport) -> str:
    lines = []
    for i, failure in enumerate(result.failure_details, start=1):
        test_name = failure.get("name", "unknown")
        item = bug_report.item_for(test_name) if bug_report else None
        title = (item.title if item and item.title else plain_title(test_name))
        description = (
            item.description if item and item.description
            else f"This automated check failed. {failure.get('error', '')[:200]}"
        )
        lines.append(f"- [ ] **{i}. {title}**\n    {description}")
    return "\n".join(lines)


async def file_bug_tickets(
    run_id: str,
    event: GitHubPushEvent,
    test_plan: TestPlan,
    result: TestResult,
    bug_report: BugReport,
) -> List[str]:
    """Create a single ClickUp task listing every failing test from this run,
    instead of one ticket per failure (which spammed the list)."""
    if not settings.clickup_enabled:
        logger.info("ClickUp posting disabled by configuration — skipping ticket creation")
        return []

    if not settings.clickup_api_token or not settings.clickup_list_id:
        logger.info("ClickUp credentials not set — skipping ticket creation")
        return []

    if not result.failure_details:
        return []

    headers = {"Authorization": settings.clickup_api_token, "Content-Type": "application/json"}
    priority = PRIORITY_MAP.get(test_plan.priority, 3)
    url = f"{CLICKUP_API_BASE}/list/{settings.clickup_list_id}/task"

    count = len(result.failure_details)
    summary = (bug_report.summary if bug_report and bug_report.summary else f"{count} automated test(s) failed.")
    payload = {
        "name": f"[ARIA] {count} failing test(s) — {event.repo_name}/{event.branch}"[:255],
        "description": (
            f"{summary}\n\n"
            f"**Repository:** {event.repo_name}\n"
            f"**Branch:** {event.branch}\n"
            f"**Run ID:** {run_id}\n\n"
            f"{_bug_items_markdown(result, bug_report)}"
        ),
        "priority": priority,
        "tags": ["aria", "automated", event.repo_name.split("/")[-1]],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            task_id = response.json().get("id", "")
            logger.info(f"ClickUp bug ticket created: {task_id} for {count} failing test(s)")
            return [task_id] if task_id else []
    except Exception as e:
        logger.error(f"ClickUp bug ticket creation failed: {e}")
        return []


def _manual_cases_markdown(manual_plan) -> str:
    lines = []
    for i, case in enumerate(manual_plan.cases, start=1):
        steps = "\n".join(f"    {n}. {s}" for n, s in enumerate(case.steps, start=1))
        lines.append(f"- [ ] **{i}. {case.title}**\n{steps}\n    - *Expected:* {case.expected}")
    return "\n".join(lines)


async def file_manual_test_ticket(run_id: str, event: GitHubPushEvent, manual_plan) -> str:
    """Create a single ClickUp task with the plain-English manual test checklist."""
    if not getattr(manual_plan, "cases", None):
        return ""
    if not settings.clickup_enabled:
        logger.info("ClickUp posting disabled — skipping manual test ticket")
        return ""
    if not settings.clickup_api_token or not settings.clickup_list_id:
        logger.info("ClickUp credentials not set — skipping manual test ticket")
        return ""

    headers = {"Authorization": settings.clickup_api_token, "Content-Type": "application/json"}
    url = f"{CLICKUP_API_BASE}/list/{settings.clickup_list_id}/task"
    payload = {
        "name": f"[ARIA] Manual QA — {event.repo_name}/{event.branch}",
        "description": (
            f"Plain-English manual test cases for a human QA engineer.\n\n"
            f"**Repository:** {event.repo_name}\n"
            f"**Branch:** {event.branch}\n"
            f"**Pushed by:** {event.author}\n"
            f"**Run ID:** {run_id}\n\n"
            f"{_manual_cases_markdown(manual_plan)}"
        ),
        "tags": ["aria", "manual-qa", event.repo_name.split("/")[-1]],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            task_id = response.json().get("id", "")
            logger.info(f"ClickUp manual QA ticket created: {task_id}")
            return task_id
    except Exception as e:
        logger.error(f"ClickUp manual test ticket creation failed: {e}")
        return ""
