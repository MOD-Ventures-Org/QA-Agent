import json

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from webhook.validator import validate_github_signature

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


def _extract_event(event_type: str, payload: dict) -> GitHubPushEvent:
    repo_name = payload.get("repository", {}).get("full_name", "unknown/unknown")
    author = (
        payload.get("sender", {}).get("login")
        or payload.get("pusher", {}).get("name", "unknown")
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
    commit_messages = [c.get("message", "") for c in commits]

    changed_files: list[str] = []
    for commit in commits:
        changed_files.extend(commit.get("added", []))
        changed_files.extend(commit.get("modified", []))
        changed_files.extend(commit.get("removed", []))
    changed_files = list(dict.fromkeys(changed_files))

    pr_title = None
    if event_type in ("pull_request", "pull_request_review"):
        pr_title = payload.get("pull_request", {}).get("title")

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
    )


def _should_run_evaluation(event: GitHubPushEvent) -> bool:
    if event.event_type == "release":
        return True
    if event.event_type in ("pull_request", "pull_request_review"):
        return True
    if event.event_type == "push" and event.branch in ("main", "master"):
        return True
    return False


def _should_ignore_event(event_type: str) -> bool:
    return event_type == "workflow_run"


async def _run_pipeline(event: GitHubPushEvent):
    from claude.analyzer import analyze_event
    from claude.test_generator import generate_tests
    from claude.evaluator import evaluate_product
    from testing.runner import run_tests
    from testing.regression_watcher import check_regression
    from storage.mongo import save_test_run, save_bug_report
    from claude.report_writer import write_bug_report
    from integrations.discord import post_discord_report
    from integrations.clickup import file_bug_tickets

    logger.info(f"Pipeline started for {event.repo_name} [{event.branch}] event={event.event_type}")

    test_plan = await analyze_event(event)
    logger.info(f"Test plan priority={test_plan.priority} reasoning={test_plan.reasoning[:80]}")

    generated_tests = None
    if test_plan.run_generated_tests:
        generated_tests = await generate_tests(event, test_plan)

    test_result = await run_tests(test_plan)
    test_result = await check_regression(event, test_result)

    evaluation = None
    if _should_run_evaluation(event):
        evaluation = await evaluate_product(event, test_plan, test_result)
        logger.info(f"Product evaluation grade={evaluation.grade} score={evaluation.quality_score} recommendation={evaluation.recommendation}")
    else:
        logger.info("Product evaluation skipped — not a PR to main, merge to main, or release")

    run_id = await save_test_run(event, test_plan, test_result, evaluation)
    logger.info(f"Saved test run id={run_id}")

    bug_summary = ""
    clickup_ids = []
    if test_result.failed > 0:
        bug_summary = await write_bug_report(test_plan, test_result)
        clickup_ids = await file_bug_tickets(run_id, event, test_plan, test_result, bug_summary)
        await save_bug_report(run_id, event, test_result, bug_summary, clickup_ids)

    discord_message_id = await post_discord_report(run_id, event, test_plan, test_result, bug_summary, evaluation, generated_tests)
    logger.info(f"Discord report posted message_id={discord_message_id}")


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    body: bytes = Depends(validate_github_signature),
):
    event_type = request.headers.get("X-GitHub-Event", "push")
    payload = json.loads(body)
    event = _extract_event(event_type, payload)
    if _should_ignore_event(event_type):
        logger.info(f"Ignoring webhook event={event_type} repo={event.repo_name} branch={event.branch}")
        return {"status": "ignored", "event": event_type}

    background_tasks.add_task(_run_pipeline, event)
    logger.info(f"Webhook received event={event_type} repo={event.repo_name} branch={event.branch}")
    return {"status": "accepted", "event": event_type}
