from typing import List

import httpx

from config import settings
from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from claude.analyzer import TestPlan
from testing.result_parser import TestResult

logger = get_logger(__name__)

PRIORITY_MAP = {"critical": 1, "high": 2, "medium": 3, "low": 4}
CLICKUP_API_BASE = "https://api.clickup.com/api/v2"


async def file_bug_tickets(
    run_id: str,
    event: GitHubPushEvent,
    test_plan: TestPlan,
    result: TestResult,
    bug_summary: str,
) -> List[str]:
    if not settings.clickup_enabled:
        logger.info("ClickUp posting disabled by configuration — skipping ticket creation")
        return []

    if not settings.clickup_api_token or not settings.clickup_list_id:
        logger.info("ClickUp credentials not set — skipping ticket creation")
        return []

    task_ids = []
    headers = {"Authorization": settings.clickup_api_token, "Content-Type": "application/json"}
    priority = PRIORITY_MAP.get(test_plan.priority, 3)
    url = f"{CLICKUP_API_BASE}/list/{settings.clickup_list_id}/task"

    async with httpx.AsyncClient(timeout=15) as client:
        for failure in result.failure_details:
            test_name = failure.get("name", "unknown")
            error_msg = failure.get("error", "")
            payload = {
                "name": f"[ARIA] {test_name} failed on {event.repo_name}/{event.branch}",
                "description": (
                    f"**Error:** {error_msg}\n\n"
                    f"**Bug Summary:** {bug_summary}\n\n"
                    f"**Run ID:** {run_id}\n"
                    f"**Branch:** {event.branch}\n"
                    f"**Author:** {event.author}"
                ),
                "priority": priority,
                "tags": ["aria", "automated", event.repo_name.split("/")[-1]],
            }
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                task_id = response.json().get("id", "")
                task_ids.append(task_id)
                logger.info(f"ClickUp task created: {task_id} for {test_name}")
            except Exception as e:
                logger.error(f"ClickUp ticket creation failed for {test_name}: {e}")

    return task_ids
