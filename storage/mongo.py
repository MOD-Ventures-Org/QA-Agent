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


async def save_test_run(event: GitHubPushEvent, test_plan: TestPlan, result: TestResult, evaluation=None, run_id: Optional[str] = None) -> str:
    run_id = run_id or str(uuid.uuid4())[:8]
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
        quality_score=evaluation.quality_score if evaluation else 0,
        grade=evaluation.grade if evaluation else "N/A",
        recommendation=evaluation.recommendation if evaluation else "unknown",
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


async def save_manual_tests(run_id: str, event: GitHubPushEvent, manual_plan) -> None:
    cases = getattr(manual_plan, "cases", None) or []
    if not cases:
        return
    doc = {
        "run_id": run_id,
        "repo": event.repo_name,
        "branch": event.branch,
        "event_type": event.event_type,
        "author": event.author,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cases": [
            {"title": c.title, "steps": list(c.steps), "expected": c.expected}
            for c in cases
        ],
    }
    try:
        db = _get_db()
        await db["manual_tests"].replace_one({"run_id": run_id}, doc, upsert=True)
        logger.info(f"Saved {len(cases)} manual test case(s) for run {run_id}")
    except Exception as e:
        logger.error(f"MongoDB save_manual_tests failed: {e}")


async def save_pipeline_output(
    run_id: str,
    event: GitHubPushEvent,
    test_plan,
    test_result: TestResult,
    generated_tests=None,
    manual_plan=None,
    evaluation=None,
    bug_summary: str = "",
    tickets: Optional[List[dict]] = None,
    status: str = "completed",
) -> None:
    manual_cases = getattr(manual_plan, "cases", None) or []
    doc = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "event": {
            "type": event.event_type,
            "repo": event.repo_name,
            "branch": event.branch,
            "author": event.author,
            "commit_messages": event.commit_messages,
            "changed_files": event.changed_files,
            "pr_title": event.pr_title or "",
        },
        "test_plan": {
            "should_test": test_plan.should_test,
            "test_kind": test_plan.test_kind,
            "priority": test_plan.priority,
            "reasoning": test_plan.reasoning,
            "focus_areas": list(test_plan.focus_areas),
            "affected_pages": list(test_plan.affected_pages),
            "pytest_keyword": getattr(test_plan, "pytest_keyword", ""),
        } if test_plan else None,
        "generated_tests": {
            "file_name": generated_tests.file_name,
            "test_names": list(generated_tests.test_names),
            "triggered_by": list(generated_tests.triggered_by),
            "code": generated_tests.code,
        } if generated_tests else None,
        "test_result": {
            "total": test_result.total,
            "passed": test_result.passed,
            "failed": test_result.failed,
            "errors": test_result.errors,
            "duration": test_result.duration,
            "regression_detected": test_result.regression_detected,
            "failure_details": test_result.failure_details,
            "suite_results": test_result.suite_results,
        },
        "manual_tests": [
            {"title": c.title, "steps": list(c.steps), "expected": c.expected}
            for c in manual_cases
        ],
        "evaluation": {
            "quality_score": evaluation.quality_score,
            "grade": evaluation.grade,
            "recommendation": evaluation.recommendation,
            "summary": evaluation.summary,
            "strengths": list(evaluation.strengths),
            "risks": list(evaluation.risks),
        } if evaluation else None,
        "bug_summary": bug_summary,
        "tickets": tickets or [],
    }
    try:
        db = _get_db()
        await db["pipeline_outputs"].replace_one({"run_id": run_id}, doc, upsert=True)
        logger.info(f"Saved pipeline output for run {run_id} to MongoDB")
    except Exception as e:
        logger.error(f"MongoDB save_pipeline_output failed: {e}")


async def save_ci_report(run_id: str, payload: dict) -> None:
    """Store the raw report POSTed back by the GitHub Actions workflow, as-is, in
    the ``ci_reports`` collection. This is the exact JSON the runner produced
    (pytest-json-report output + CI metadata) so a dev can inspect it later.
    """
    doc = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo": payload.get("repo", ""),
        "branch": payload.get("branch", ""),
        "event": payload.get("event", ""),
        "sha": payload.get("sha", ""),
        "actor": payload.get("actor", ""),
        "run_url": payload.get("run_url", ""),
        "report": payload.get("report") or {},
    }
    try:
        db = _get_db()
        await db["ci_reports"].replace_one({"run_id": run_id}, doc, upsert=True)
        logger.info(f"Saved CI report for run {run_id} to MongoDB (ci_reports)")
    except Exception as e:
        logger.error(f"MongoDB save_ci_report failed: {e}")


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
