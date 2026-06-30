"""ARIA's reporting brain for the hybrid pipeline.

GitHub Actions runs the ARIA-generated tests in the runner and POSTs the JSON
report here. This module turns that report into everything that makes ARIA a
product: a Claude product-evaluation, plain-English manual test cases, a bug
summary, Discord notifications, ClickUp tickets, MongoDB persistence, and a
dashboard run record.

No cloning, no test generation, no pushing happens here — that is the cheap
in-runner / webhook-pipeline half. This is the downstream half.
"""

import uuid

from utils.logger import get_logger
from webhook.models import GitHubPushEvent

logger = get_logger(__name__)


async def process_results(payload: dict) -> dict:
    # Lazy imports keep app startup light and mirror the existing pipeline style.
    from claude.analyzer import TestPlan
    from claude.client import AIQuotaExceededError
    from claude.evaluator import evaluate_product
    from claude.manual_tests import generate_manual_tests
    from claude.report_writer import write_bug_report, BugReport
    from integrations.clickup import file_bug_tickets, file_manual_test_ticket
    from integrations.discord import post_discord_report
    from storage import runs
    from storage.mongo import (
        save_bug_report,
        save_ci_report,
        save_manual_tests,
        save_pipeline_output,
        save_test_run,
    )
    from testing.result_parser import parse_pytest_dict

    repo = payload.get("repo", "unknown/unknown")
    branch = payload.get("branch", "")
    event_name = payload.get("event", "workflow")
    actor = payload.get("actor", "unknown")
    run_url = payload.get("run_url", "")
    report = payload.get("report") or {}

    result = parse_pytest_dict(report)
    run_id = str(uuid.uuid4())[:8]
    has_failures = (result.failed or 0) > 0 or (result.errors or 0) > 0

    logger.info(
        "Results callback run_id=%s repo=%s branch=%s event=%s total=%d passed=%d failed=%d errors=%d",
        run_id, repo, branch, event_name, result.total, result.passed, result.failed, result.errors,
    )

    event = GitHubPushEvent(
        event_type=event_name,
        repo_name=repo,
        branch=branch,
        author=actor,
        commit_messages=[],
        changed_files=[],
        diff_summary=f"CI report: {result.total} test(s)",
        pr_title=None,
    )
    test_plan = TestPlan(
        reasoning="ARIA-generated suite executed in GitHub Actions (hybrid runner).",
        should_test=True,
        test_kind="mixed",
        priority="high" if has_failures else "medium",
    )

    await runs.create_run(run_id, event)
    # Persist the raw runner report first, so it's stored even if AI steps fail.
    await save_ci_report(run_id, payload)

    # --- AI-written artifacts (each degrades gracefully; quota errors don't abort) ---
    evaluation = None
    try:
        evaluation = await evaluate_product(event, test_plan, result)
    except AIQuotaExceededError as exc:
        logger.error("Evaluator skipped — AI quota exceeded: %s", exc)

    bug_report = BugReport(summary="No failures to report.")
    if has_failures:
        try:
            bug_report = await write_bug_report(test_plan, result)
        except AIQuotaExceededError as exc:
            logger.error("Bug report skipped — AI quota exceeded: %s", exc)

    manual_plan = None
    try:
        manual_plan = await generate_manual_tests(event)
    except AIQuotaExceededError as exc:
        logger.error("Manual tests skipped — AI quota exceeded: %s", exc)

    # --- Tickets ---
    bug_task_ids = []
    if has_failures:
        bug_task_ids = await file_bug_tickets(run_id, event, test_plan, result, bug_report)
    manual_task_id = await file_manual_test_ticket(run_id, event, manual_plan) if manual_plan else ""
    tickets = [{"type": "bug", "id": t} for t in bug_task_ids]
    if manual_task_id:
        tickets.append({"type": "manual", "id": manual_task_id})

    # --- Discord ---
    discord_id = await post_discord_report(
        run_id, event, test_plan, result, bug_report.summary,
        evaluation=evaluation, manual_plan=manual_plan,
        mongo_persisted=True, run_link=run_url,
    )

    # --- Persistence ---
    await save_test_run(event, test_plan, result, evaluation, run_id=run_id)
    if manual_plan:
        await save_manual_tests(run_id, event, manual_plan)
    if has_failures:
        await save_bug_report(run_id, event, result, bug_report.summary, bug_task_ids, discord_id)
    await save_pipeline_output(
        run_id, event, test_plan, result,
        manual_plan=manual_plan, evaluation=evaluation,
        bug_summary=bug_report.summary, tickets=tickets, status="completed",
    )

    # --- Dashboard run record ---
    await runs.patch_run(
        run_id,
        status="completed",
        test_result={
            "total": result.total, "passed": result.passed,
            "failed": result.failed, "errors": result.errors,
            "duration": result.duration, "failure_details": result.failure_details,
        },
        evaluation=(
            {
                "quality_score": evaluation.quality_score,
                "grade": evaluation.grade,
                "recommendation": evaluation.recommendation,
                "summary": evaluation.summary,
            } if evaluation else None
        ),
        bug_summary=bug_report.summary,
        tickets=tickets,
        discord_message_id=discord_id,
        run_url=run_url,
    )

    return {"run_id": run_id, "passed": result.passed, "failed": result.failed, "errors": result.errors}
