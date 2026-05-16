import uuid
from datetime import datetime, timezone
from typing import List, Optional

import motor.motor_asyncio

from config import settings
from utils.logger import get_logger
from storage.models import BugReportDocument, TestRunDocument
from webhook.models import GitHubPushEvent
from claude.analyzer import TestPlan
from testing.result_parser import TestResult

logger = get_logger(__name__)

_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None


def _get_db():
    global _client
    if _client is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(settings.mongodb_uri)
    return _client[settings.mongodb_db_name]


async def save_test_run(event: GitHubPushEvent, test_plan: TestPlan, result: TestResult) -> str:
    run_id = str(uuid.uuid4())[:8]
    doc = TestRunDocument(
        run_id=run_id,
        repo=event.repo_name,
        branch=event.branch,
        event_type=event.event_type,
        timestamp=datetime.now(timezone.utc).isoformat(),
        priority=test_plan.priority,
        reasoning=test_plan.reasoning,
        suite_results=result.suite_results,
        total=result.total,
        passed=result.passed,
        failed=result.failed,
        duration=result.duration,
        regression_detected=result.regression_detected,
    )
    try:
        db = _get_db()
        await db["test_runs"].insert_one(doc.to_dict())
        logger.info(f"Saved test run {run_id} to MongoDB")
    except Exception as e:
        logger.error(f"MongoDB save_test_run failed: {e}")
    return run_id


async def save_bug_report(
    run_id: str,
    event: GitHubPushEvent,
    result: TestResult,
    claude_summary: str,
    clickup_task_ids: List[str],
    discord_message_id: str = "",
):
    doc = BugReportDocument(
        run_id=run_id,
        repo=event.repo_name,
        branch=event.branch,
        failed_tests=result.failure_details,
        claude_summary=claude_summary,
        clickup_task_ids=clickup_task_ids,
        discord_message_id=discord_message_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    try:
        db = _get_db()
        await db["bug_reports"].replace_one(
            {"run_id": run_id},
            doc.to_dict(),
            upsert=True,
        )
        logger.info(f"Saved bug report for run {run_id}")
    except Exception as e:
        logger.error(f"MongoDB save_bug_report failed: {e}")


async def get_recent_runs(repo: str, branch: str, limit: int = 5) -> List[dict]:
    try:
        db = _get_db()
        cursor = db["test_runs"].find(
            {"repo": repo, "branch": branch},
            sort=[("timestamp", -1)],
            limit=limit,
        )
        return await cursor.to_list(length=limit)
    except Exception as e:
        logger.error(f"MongoDB get_recent_runs failed: {e}")
        return []
