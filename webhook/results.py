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

    sha = payload.get("sha", "")
    result = parse_pytest_dict(report)
    has_failures = (result.failed or 0) > 0 or (result.errors or 0) > 0

    # Attach to the generation run that produced these tests (one unified dashboard
    # run). If there's none (identical-diff skip, or a repo using CI mode), start a
    # fresh run and mark the generation steps as not-recorded-here.
    existing = await runs.find_open_run(repo, branch, sha)
    attached = existing is not None
    run_id = existing or str(uuid.uuid4())[:8]

    logger.info(
        "Results callback run_id=%s attached=%s repo=%s branch=%s event=%s total=%d passed=%d failed=%d errors=%d",
        run_id, attached, repo, branch, event_name, result.total, result.passed, result.failed, result.errors,
    )

    event = GitHubPushEvent(
        event_type=event_name,
        repo_name=repo,
        branch=branch,
        author=actor,
        commit_messages=[],
        changed_files=[],
        diff_summary=f"CI report: {result.total} test(s)",
        sha=sha,
        pr_title=None,
    )
    test_plan = TestPlan(
        reasoning="ARIA-generated suite executed in GitHub Actions (hybrid runner).",
        should_test=True,
        test_kind="mixed",
        priority="high" if has_failures else "medium",
    )

    if not attached:
        await runs.create_run(run_id, event)
        # No generation record to reuse — those steps happened elsewhere / earlier.
        for key in ("clone", "analyze", "generate", "push"):
            await runs.finish_step(run_id, key, status="skipped", output="handled outside this run")

    # GitHub Actions finished and POSTed the report back — close the hand-off step.
    await runs.finish_step(
        run_id, "actions",
        output=f"{result.total} test(s) executed in GitHub Actions ({result.passed} passed, {result.failed} failed)",
    )

    # Persist the raw runner report first, so it's stored even if AI steps fail.
    await save_ci_report(run_id, payload)
    await runs.finish_step(
        run_id, "parse",
        output=f"{result.total} test(s): {result.passed} passed, {result.failed} failed, {result.errors} error(s)",
    )

    # --- AI-written artifacts (each degrades gracefully; quota errors don't abort) ---
    evaluation = None
    await runs.start_step(run_id, "evaluate")
    try:
        evaluation = await evaluate_product(event, test_plan, result)
        await runs.finish_step(
            run_id, "evaluate",
            output=(f"{evaluation.grade} · {evaluation.quality_score}/100 · {evaluation.recommendation}"
                    if evaluation else "no evaluation"),
        )
    except AIQuotaExceededError as exc:
        logger.error("Evaluator skipped — AI quota exceeded: %s", exc)
        await runs.finish_step(run_id, "evaluate", status="skipped", error="AI quota exceeded")

    bug_report = BugReport(summary="No failures to report.")
    if has_failures:
        await runs.start_step(run_id, "bug_report")
        try:
            bug_report = await write_bug_report(test_plan, result)
            await runs.finish_step(run_id, "bug_report", output=bug_report.summary[:200])
        except AIQuotaExceededError as exc:
            logger.error("Bug report skipped — AI quota exceeded: %s", exc)
            await runs.finish_step(run_id, "bug_report", status="skipped", error="AI quota exceeded")
    else:
        await runs.finish_step(run_id, "bug_report", status="skipped", output="No failures to report")

    manual_plan = None
    await runs.start_step(run_id, "manual")
    try:
        manual_plan = await generate_manual_tests(event)
        n_cases = len(getattr(manual_plan, "cases", []) or []) if manual_plan else 0
        await runs.finish_step(run_id, "manual", output=f"{n_cases} manual case(s)")
    except AIQuotaExceededError as exc:
        logger.error("Manual tests skipped — AI quota exceeded: %s", exc)
        await runs.finish_step(run_id, "manual", status="skipped", error="AI quota exceeded")

    # --- Tickets + Discord ---
    await runs.start_step(run_id, "notify")
    bug_task_ids = []
    if has_failures:
        bug_task_ids = await file_bug_tickets(run_id, event, test_plan, result, bug_report)
    manual_task_id = await file_manual_test_ticket(run_id, event, manual_plan) if manual_plan else ""
    tickets = [{"type": "bug", "id": t} for t in bug_task_ids]
    if manual_task_id:
        tickets.append({"type": "manual", "id": manual_task_id})

    discord_id = await post_discord_report(
        run_id, event, test_plan, result, bug_report.summary,
        evaluation=evaluation, manual_plan=manual_plan,
        mongo_persisted=True, run_link=run_url,
    )
    await runs.finish_step(
        run_id, "notify",
        output=f"{len(tickets)} ticket(s)" + (" · Discord sent" if discord_id else ""),
    )

    # --- Persistence ---
    await runs.start_step(run_id, "persist")
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
    await runs.finish_step(run_id, "persist", output="Saved to MongoDB")

    # --- Dashboard run record ---
    # Only set test_plan when we created this run; an attached generation run already
    # carries the real analyzer plan and must not be clobbered with the synthetic one.
    dashboard_fields = {} if attached else {
        "test_plan": {
            "should_test": test_plan.should_test,
            "test_kind": test_plan.test_kind,
            "priority": test_plan.priority,
            "reasoning": test_plan.reasoning,
        }
    }
    await runs.patch_run(
        run_id,
        status="completed",
        **dashboard_fields,
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
