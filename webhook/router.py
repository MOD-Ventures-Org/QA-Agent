"""Webhook receiver for GitHub events.

Pipeline: ai_check → clone → analyze → generate tests → generate workflow → push to repo.
GitHub Actions executes the generated workflow; results are visible in the Actions tab.
No local test execution, no Discord reporting, no dashboard updates.
"""

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from utils.logger import get_logger
from claude.workflow_generator import ARIA_COMMIT_MARKER
from webhook.models import GitHubPushEvent
from webhook.validator import validate_github_signature

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


def _extract_event(event_type: str, payload: dict) -> GitHubPushEvent:
    repo_name = payload.get("repository", {}).get("full_name", "unknown/unknown")
    author = (
        payload.get("sender", {}).get("login")
        or payload.get("pusher", {}).get("name")
        or payload.get("deployment", {}).get("creator", {}).get("login")
        or "unknown"
    )

    branch = ""
    if event_type == "push":
        branch = payload.get("ref", "").replace("refs/heads/", "")
    elif event_type in ("pull_request", "pull_request_review"):
        branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
    elif event_type in ("deployment", "deployment_status"):
        branch = (
            payload.get("deployment", {}).get("ref", "")
            or payload.get("deployment_status", {}).get("deployment", {}).get("ref", "")
        )
    elif event_type == "workflow_run":
        branch = payload.get("workflow_run", {}).get("head_branch", "")

    commits = payload.get("commits", [])
    commit_messages = [c.get("message", "") for c in commits if c.get("message")]
    if not commit_messages:
        head_message = (payload.get("head_commit") or {}).get("message")
        if head_message:
            commit_messages = [head_message]

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

    deployment_state = None
    if event_type == "deployment_status":
        deployment_state = payload.get("deployment_status", {}).get("state")

    return GitHubPushEvent(
        event_type=event_type,
        repo_name=repo_name,
        branch=branch,
        author=author,
        commit_messages=commit_messages,
        changed_files=changed_files,
        diff_summary=f"{len(commits)} commit(s) touching {len(changed_files)} file(s)",
        pr_title=pr_title,
        action=payload.get("action"),
        merged=merged,
        base_branch=base_branch,
        deployment_state=deployment_state,
    )


MAIN_BRANCHES = ("main", "master")
PR_ACTIONS = ("opened", "synchronize", "reopened")


def _should_process_event(event: GitHubPushEvent) -> bool:
    # Ignore commits ARIA itself pushed (workflow + test files) — prevents infinite loops.
    if any(ARIA_COMMIT_MARKER in (msg or "") for msg in event.commit_messages):
        return False
    if event.event_type == "pull_request":
        return event.base_branch in MAIN_BRANCHES and event.action in PR_ACTIONS
    if event.event_type == "push":
        return event.branch in MAIN_BRANCHES
    if event.event_type == "deployment_status":
        return event.deployment_state == "success"
    return False


async def _run_pipeline(event: GitHubPushEvent):
    import uuid
    from config import settings
    from claude.analyzer import analyze_event, ai_reachable
    from claude.repo_context import build_repo_context
    from claude.test_generator import generate_tests
    from claude.workflow_generator import generate_workflow, ARIA_COMMIT_MARKER
    from integrations.github_push import push_files_to_branch

    run_id = str(uuid.uuid4())[:8]
    logger.info(
        "Pipeline started run_id=%s repo=%s branch=%s event=%s",
        run_id, event.repo_name, event.branch, event.event_type,
    )

    # 1. Verify the AI service is reachable before doing any work.
    if not ai_reachable():
        logger.error(
            "run_id=%s AI service unreachable — skipping pipeline for %s [%s]",
            run_id, event.repo_name, event.branch,
        )
        return

    # 2. Clone the repo and extract README / file tree / changed-file contents.
    repo_context = build_repo_context(event, settings.github_token)
    logger.info("run_id=%s repo_type=%s cloned=%s", run_id, repo_context.repo_type, repo_context.cloned)

    try:
        # 3. Analyze the change: decide whether to test and what kind of tests fit.
        test_plan = await analyze_event(event, repo_context)
        logger.info(
            "run_id=%s should_test=%s test_kind=%s priority=%s reasoning=%s",
            run_id, test_plan.should_test, test_plan.test_kind,
            test_plan.priority, test_plan.reasoning[:100],
        )

        if not test_plan.should_test:
            logger.info("run_id=%s change not worth testing — pipeline complete", run_id)
            return

        # 4. Generate pytest/Playwright tests customized to the actual code change.
        generated_tests = await generate_tests(event, test_plan, repo_context)
        if not generated_tests:
            logger.error("run_id=%s test generation failed — aborting", run_id)
            return
        logger.info(
            "run_id=%s generated %d test(s) in %s",
            run_id, len(generated_tests.test_names), generated_tests.file_name,
        )

        # 5. Generate a GitHub Actions workflow YAML customized to this repo and change.
        generated_workflow = await generate_workflow(event, test_plan, generated_tests, repo_context)
        if not generated_workflow:
            logger.error("run_id=%s workflow generation failed — aborting", run_id)
            return
        logger.info("run_id=%s generated workflow %s", run_id, generated_workflow.filename)

        # 6. Push the test file + workflow to the branch.
        #    GitHub Actions picks them up automatically; results appear in the Actions tab.
        files_to_push = {
            f"testing/suites/generated/{generated_tests.file_name}": generated_tests.code,
            generated_workflow.filename: generated_workflow.content,
        }
        commit_msg = f"chore(aria): tests + workflow for run {run_id} [{ARIA_COMMIT_MARKER}]"

        if not settings.github_token:
            logger.error("run_id=%s GITHUB_TOKEN not configured — cannot push to repo", run_id)
            return

        pushed = await push_files_to_branch(
            event.repo_name, event.branch, files_to_push,
            settings.github_token, commit_message=commit_msg,
        )
        if pushed:
            logger.info(
                "run_id=%s pushed %d file(s) to %s@%s — GitHub Actions will run the tests",
                run_id, len(files_to_push), event.repo_name, event.branch,
            )
        else:
            logger.error(
                "run_id=%s push failed for %s@%s — verify GITHUB_TOKEN has contents:write permission",
                run_id, event.repo_name, event.branch,
            )
    finally:
        repo_context.cleanup()


async def _run_pipeline_safe(event: GitHubPushEvent):
    from claude.client import AIQuotaExceededError
    try:
        await _run_pipeline(event)
    except AIQuotaExceededError as exc:
        logger.error(
            "AI quota exceeded for %s [%s]: %s — top up credits and re-run",
            event.repo_name, event.branch, exc,
        )
    except Exception as exc:
        logger.exception(
            "Pipeline failed for %s [%s] event=%s: %s",
            event.repo_name, event.branch, event.event_type, exc,
        )


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
            "Skipping event=%s repo=%s branch=%s — not a qualifying event",
            event_type, event.repo_name, event.branch,
        )
        return {"status": "skipped", "event": event_type}

    background_tasks.add_task(_run_pipeline_safe, event)
    logger.info(
        "Webhook accepted event=%s repo=%s branch=%s",
        event_type, event.repo_name, event.branch,
    )
    return {"status": "accepted", "event": event_type}
