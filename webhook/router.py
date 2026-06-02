import json

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from utils.logger import get_logger
from claude.analyzer import TestPlan
from webhook.models import GitHubPushEvent
from webhook.validator import validate_github_signature

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


def _extract_event(event_type: str, payload: dict) -> GitHubPushEvent:
    repo_name = payload.get("repository", {}).get("full_name", "unknown/unknown")
    author = (
        payload.get("sender", {}).get("login")
        or payload.get("pusher", {}).get("name")
        or payload.get("release", {}).get("author", {}).get("login")
        or payload.get("deployment", {}).get("creator", {}).get("login")
        or "unknown"
    )

    branch = ""
    if event_type == "push":
        branch = payload.get("ref", "").replace("refs/heads/", "")
    elif event_type in ("pull_request", "pull_request_review"):
        branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
    elif event_type == "release":
        branch = payload.get("release", {}).get("tag_name", "")
    elif event_type in ("deployment", "deployment_status"):
        branch = payload.get("deployment", {}).get("ref", "") or payload.get("deployment_status", {}).get("deployment", {}).get("ref", "")
    elif event_type == "workflow_run":
        branch = payload.get("workflow_run", {}).get("head_branch", "")

    commits = payload.get("commits", [])
    commit_messages = [c.get("message", "") for c in commits if c.get("message")]

    # Deployment/release payloads carry no "commits" array — derive a commit label.
    if not commit_messages:
        head_message = (payload.get("head_commit") or {}).get("message")
        if head_message:
            commit_messages = [head_message]
        elif event_type in ("deployment", "deployment_status"):
            deployment = payload.get("deployment", {}) or payload.get("deployment_status", {}).get("deployment", {})
            if deployment.get("description"):
                commit_messages = [deployment["description"]]
        elif event_type == "release":
            release = payload.get("release", {})
            label = release.get("name") or release.get("tag_name") or release.get("body")
            if label:
                commit_messages = [label]

    changed_files: list[str] = []
    for commit in commits:
        changed_files.extend(commit.get("added", []))
        changed_files.extend(commit.get("modified", []))
        changed_files.extend(commit.get("removed", []))
    changed_files = list(dict.fromkeys(changed_files))

    pr_title = None
    merged = None
    base_branch = None
    if event_type in ("pull_request", "pull_request_review"):
        pull_request = payload.get("pull_request", {})
        pr_title = pull_request.get("title")
        merged = bool(pull_request.get("merged"))
        base_branch = pull_request.get("base", {}).get("ref")

    action = payload.get("action")

    diff_summary = (
        f"{len(commits)} commit(s) touching {len(changed_files)} file(s)"
    )

    return GitHubPushEvent(
        event_type=event_type,
        repo_name=repo_name,
        branch=branch,
        author=author,
        commit_messages=commit_messages,
        changed_files=changed_files,
        diff_summary=diff_summary,
        pr_title=pr_title,
        action=action,
        merged=merged,
        base_branch=base_branch,
    )


def _should_run_evaluation(event: GitHubPushEvent) -> bool:
    if event.event_type == "release":
        return True
    if event.event_type in ("pull_request", "pull_request_review"):
        return True
    if event.event_type == "push" and event.branch in ("main", "master"):
        return True
    return False


DEPLOY_EVENTS = ("deployment", "deployment_status", "release")
MAIN_BRANCHES = ("main", "master")


def _should_process_event(event: GitHubPushEvent) -> bool:
    """Run the QA pipeline only for merged code (PR merge into any branch, or a
    push landing on main/master) and deployment/release events."""
    if event.event_type in DEPLOY_EVENTS:
        return True
    if event.event_type in ("pull_request", "pull_request_review"):
        # A merge into main/master also arrives as a push event (which carries the
        # commits/diff), so process that one instead — avoids duplicate reports.
        return bool(event.merged) and event.base_branch not in MAIN_BRANCHES
    if event.event_type == "push":
        return event.branch in MAIN_BRANCHES
    return False


def _plan_dict(test_plan) -> dict:
    return {
        "should_test": test_plan.should_test,
        "test_kind": test_plan.test_kind,
        "priority": test_plan.priority,
        "reasoning": test_plan.reasoning,
        "focus_areas": list(test_plan.focus_areas),
        "affected_pages": list(test_plan.affected_pages),
    }


def _result_dict(result) -> dict:
    return {
        "total": result.total,
        "passed": result.passed,
        "failed": result.failed,
        "errors": result.errors,
        "duration": result.duration,
        "regression_detected": result.regression_detected,
        "failure_details": result.failure_details,
        "suite_results": result.suite_results,
    }


def _generated_dict(generated) -> dict:
    if generated is None:
        return None
    return {
        "file_name": generated.file_name,
        "test_names": list(generated.test_names),
        "triggered_by": list(generated.triggered_by),
        "code": generated.code,
    }


def _manual_dict(manual_plan) -> list:
    return [
        {"title": c.title, "steps": list(c.steps), "expected": c.expected}
        for c in (getattr(manual_plan, "cases", None) or [])
    ]


def _eval_dict(evaluation) -> dict:
    if evaluation is None:
        return None
    return {
        "quality_score": evaluation.quality_score,
        "grade": evaluation.grade,
        "recommendation": evaluation.recommendation,
        "summary": evaluation.summary,
        "strengths": list(evaluation.strengths),
        "risks": list(evaluation.risks),
    }


async def _run_pipeline(event: GitHubPushEvent):
    import uuid

    from config import settings
    from claude.analyzer import TestPlan, analyze_event, ai_reachable
    from claude.repo_context import build_repo_context
    from claude.test_generator import generate_tests
    from claude.manual_tests import generate_manual_tests
    from claude.evaluator import evaluate_product
    from claude.report_writer import write_bug_report
    from integrations.clickup import file_bug_tickets, file_manual_test_ticket
    from testing.runner import run_tests
    from testing.regression_watcher import check_regression
    from testing.result_parser import TestResult
    from storage.mongo import save_bug_report, save_manual_tests, save_test_run
    from storage import runs
    from integrations.discord import post_discord_report, post_ai_unavailable_report, post_run_started
    from runtime import run_link as build_run_link

    run_id = str(uuid.uuid4())[:8]
    link = build_run_link(run_id)
    logger.info(f"Pipeline started run_id={run_id} for {event.repo_name} [{event.branch}] event={event.event_type}")

    # Create the run record and ping Discord immediately so the dashboard link works
    # while the pipeline is still running.
    await runs.create_run(run_id, event)
    await post_run_started(event, run_id, link)

    # If the AI service can't be reached, don't run anything or post a misleading
    # report — send one concise alert and stop. No tests, tickets, or test cases.
    await runs.start_step(run_id, "ai_check")
    if not ai_reachable():
        logger.error(f"AI service unreachable — skipping pipeline for {event.repo_name} [{event.branch}]")
        await runs.finish_step(run_id, "ai_check", status="failed", error="AI service could not be reached (network/DNS)")
        await runs.patch_run(run_id, status="failed")
        await post_ai_unavailable_report(event, "AI service could not be reached (network/DNS). No analysis was performed.")
        return
    await runs.finish_step(run_id, "ai_check", output="AI service reachable")

    await runs.start_step(run_id, "clone")
    repo_context = build_repo_context(event, settings.github_token)
    await runs.finish_step(run_id, "clone", output=f"type={repo_context.repo_type} cloned={repo_context.cloned}")
    try:
        await runs.start_step(run_id, "analyze")
        test_plan = await analyze_event(event, repo_context)
        await runs.patch_run(run_id, test_plan=_plan_dict(test_plan))
        await runs.finish_step(
            run_id, "analyze",
            output=f"should_test={test_plan.should_test} test_kind={test_plan.test_kind} priority={test_plan.priority}",
        )
        logger.info(f"Repo type={repo_context.repo_type} cloned={repo_context.cloned}")
        logger.info(
            f"should_test={test_plan.should_test} test_kind={test_plan.test_kind} "
            f"priority={test_plan.priority} keyword={test_plan.pytest_keyword!r}"
        )

        # Plain-English manual test cases for a human QA engineer (always produced).
        await runs.start_step(run_id, "manual_tests")
        manual_plan = await generate_manual_tests(event, repo_context)
        await runs.patch_run(run_id, manual_tests=_manual_dict(manual_plan))
        await runs.finish_step(run_id, "manual_tests", output=f"{len(_manual_dict(manual_plan))} case(s)")

        # Generate change-specific tests and run them only when the change is worth testing.
        generated_tests = None
        test_result = TestResult()
        if test_plan.should_test:
            await runs.start_step(run_id, "generate")
            generated_tests = await generate_tests(event, test_plan, repo_context)
            await runs.patch_run(run_id, generated_tests=_generated_dict(generated_tests))
            gen_count = len(generated_tests.test_names) if generated_tests else 0
            await runs.finish_step(run_id, "generate", output=f"{gen_count} test(s) generated")

            await runs.start_step(run_id, "run_tests")
            test_result = await run_tests(test_plan)
            await runs.patch_run(run_id, test_result=_result_dict(test_result))
            await runs.finish_step(
                run_id, "run_tests",
                output=f"passed={test_result.passed} failed={test_result.failed} errors={test_result.errors} ({test_result.duration:.1f}s)",
            )

            await runs.start_step(run_id, "regression")
            test_result = await check_regression(event, test_result)
            await runs.patch_run(run_id, test_result=_result_dict(test_result))
            await runs.finish_step(run_id, "regression", output=f"regression_detected={test_result.regression_detected}")
        else:
            logger.info("Change not worth testing (should_test=false) — skipping generation/run")
            for key in ("generate", "run_tests", "regression"):
                await runs.finish_step(run_id, key, status="skipped", output="change not worth testing")
    finally:
        repo_context.cleanup()

    evaluation = None
    await runs.start_step(run_id, "evaluate")
    if _should_run_evaluation(event):
        evaluation = await evaluate_product(event, test_plan, test_result, repo_context)
        await runs.patch_run(run_id, evaluation=_eval_dict(evaluation))
        await runs.finish_step(
            run_id, "evaluate",
            output=f"grade={evaluation.grade} score={evaluation.quality_score} recommendation={evaluation.recommendation}",
        )
        logger.info(f"Product evaluation grade={evaluation.grade} score={evaluation.quality_score} recommendation={evaluation.recommendation}")
    else:
        await runs.finish_step(run_id, "evaluate", status="skipped", output="not a PR to main, merge to main, or release")
        logger.info("Product evaluation skipped — not a PR to main, merge to main, or release")

    bug_summary = ""
    tickets: list = []

    # Errored run (tests could not run reliably). Report the failure to Discord
    # only — do NOT save to MongoDB (test_runs) and do NOT create any ClickUp tickets.
    if test_result.errors > 0:
        logger.warning(
            f"Errors detected (errors={test_result.errors}) on {event.repo_name} [{event.branch}] "
            f"— Discord report only; skipping test_runs save and ClickUp tickets"
        )
        await runs.finish_step(run_id, "persist", status="skipped", output="errored run — not saved to test_runs")
        bug_summary = await write_bug_report(test_plan, test_result)
        await runs.patch_run(run_id, bug_summary=bug_summary)
        await runs.finish_step(run_id, "tickets", status="skipped", output="errored run — no tickets created")

        await runs.start_step(run_id, "report")
        discord_message_id = await post_discord_report(
            run_id, event, test_plan, test_result, bug_summary, evaluation, generated_tests, manual_plan, run_link=link
        )
        await runs.finish_step(run_id, "report", output="posted")
        await runs.patch_run(run_id, status="failed", discord_message_id=discord_message_id)
        logger.info(f"Discord report posted message_id={discord_message_id}")
        return

    await runs.start_step(run_id, "persist")
    await save_test_run(event, test_plan, test_result, evaluation, run_id=run_id)
    # Persist the manual test cases for human QA (history + Discord). Tickets are
    # only created when there are bugs (see below).
    await save_manual_tests(run_id, event, manual_plan)
    await runs.finish_step(run_id, "persist", output="saved to MongoDB")
    logger.info(f"Saved test run id={run_id}")

    await runs.start_step(run_id, "tickets")
    if test_result.failed > 0:
        logger.warning(f"Tests failed: {test_result.failed} failure(s) on {event.repo_name} [{event.branch}]")
        for failure in (test_result.failure_details or []):
            logger.warning(f"  FAILED: {failure.get('name')} — {failure.get('error', '')[:200]}")

        bug_summary = await write_bug_report(test_plan, test_result)
        clickup_ids = await file_bug_tickets(run_id, event, test_plan, test_result, bug_summary)
        await save_bug_report(run_id, event, test_result, bug_summary, clickup_ids)
        tickets = [{"id": tid, "url": f"https://app.clickup.com/t/{tid}"} for tid in (clickup_ids or [])]

        # Bugs found — also file a manual QA checklist ticket for the affected change.
        manual_ticket_id = await file_manual_test_ticket(run_id, event, manual_plan)
        if manual_ticket_id:
            tickets.append({"id": manual_ticket_id, "url": f"https://app.clickup.com/t/{manual_ticket_id}", "kind": "manual"})
            logger.info(f"Manual QA ticket filed: {manual_ticket_id}")
        await runs.patch_run(run_id, bug_summary=bug_summary, tickets=tickets)
        await runs.finish_step(run_id, "tickets", output=f"{len(tickets)} ticket(s) filed")
    else:
        logger.info("No failures — no ClickUp tickets created")
        await runs.finish_step(run_id, "tickets", status="skipped", output="no failures — no tickets")

    await runs.start_step(run_id, "report")
    discord_message_id = await post_discord_report(
        run_id,
        event,
        test_plan,
        test_result,
        bug_summary,
        evaluation,
        generated_tests,
        manual_plan,
        mongo_persisted=True,
        run_link=link,
    )
    await runs.finish_step(run_id, "report", output="posted")
    await runs.patch_run(run_id, status="completed", discord_message_id=discord_message_id)
    logger.info(f"Discord report posted message_id={discord_message_id}")


async def _run_pipeline_safe(event: GitHubPushEvent):
    try:
        await _run_pipeline(event)
    except Exception as exc:
        logger.exception(
            "Pipeline failed for %s [%s] event=%s: %s",
            event.repo_name,
            event.branch,
            event.event_type,
            exc,
        )
        from claude.analyzer import TestPlan
        from testing.result_parser import TestResult
        from integrations.discord import post_discord_report

        error_plan = TestPlan(reasoning="Pipeline failure", priority="critical")
        error_result = TestResult(
            total=0,
            passed=0,
            failed=0,
            errors=1,
            duration=0.0,
            failure_details=[{"name": "pipeline", "error": str(exc), "traceback": ""}],
        )
        try:
            await post_discord_report(
                "pipeline-error",
                event,
                error_plan,
                error_result,
                f"Pipeline exception: {exc}",
            )
        except Exception as discord_exc:
            logger.exception("Failed to send pipeline failure Discord report: %s", discord_exc)


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    body: bytes = Depends(validate_github_signature),
):
    event_type = request.headers.get("X-GitHub-Event", "push")
    payload = json.loads(body)
    event = _extract_event(event_type, payload)
    if not _should_process_event(event):
        logger.info(
            f"Skipping event={event_type} repo={event.repo_name} branch={event.branch} "
            f"merged={event.merged} — not a merge/main push or deployment"
        )
        return {"status": "skipped", "event": event_type}

    background_tasks.add_task(_run_pipeline_safe, event)
    logger.info(f"Webhook received event={event_type} repo={event.repo_name} branch={event.branch}")
    return {"status": "accepted", "event": event_type}
