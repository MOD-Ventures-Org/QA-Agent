"""Webhook receiver for GitHub events (generation half of the hybrid pipeline).

Pipeline: ai_check → clone → fingerprint check → analyze → generate tests →
push tests + static workflow (once) → mark fingerprint.

The committed workflow (integrations/static_workflow.py) is generic and runs the
generated tests in GitHub Actions, then POSTs the report back to /webhook/results,
where the reporting brain (Discord, ClickUp, eval report, dashboard) takes over.
This module does no test execution and no reporting itself.
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

    # Commit SHA — used to correlate this generation run with the later results
    # callback (which reports $GITHUB_SHA) so both halves land on one dashboard run.
    sha = ""
    if event_type == "push":
        sha = payload.get("after", "") or (payload.get("head_commit") or {}).get("id", "")
    elif event_type in ("pull_request", "pull_request_review"):
        sha = payload.get("pull_request", {}).get("head", {}).get("sha", "")
    elif event_type in ("deployment", "deployment_status"):
        sha = (
            payload.get("deployment", {}).get("sha", "")
            or payload.get("deployment_status", {}).get("deployment", {}).get("sha", "")
        )

    return GitHubPushEvent(
        event_type=event_type,
        repo_name=repo_name,
        branch=branch,
        author=author,
        commit_messages=commit_messages,
        changed_files=changed_files,
        diff_summary=f"{len(commits)} commit(s) touching {len(changed_files)} file(s)",
        sha=sha,
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
    from claude.client import AIQuotaExceededError
    from claude.repo_context import build_repo_context
    from claude.test_generator import generate_tests
    from claude.workflow_generator import ARIA_COMMIT_MARKER
    from integrations.github_push import push_files_to_branch
    from integrations.static_workflow import STATIC_WORKFLOW_PATH, STATIC_WORKFLOW_CONTENT
    from integrations.conftest_template import CONFTEST_PATH, CONFTEST_CONTENT
    from storage import fingerprints, runs

    run_id = str(uuid.uuid4())[:8]
    logger.info(
        "Pipeline started run_id=%s repo=%s branch=%s event=%s",
        run_id, event.repo_name, event.branch, event.event_type,
    )

    # Create the dashboard run up front so the generation half is visible live; the
    # reporting half later attaches to this same run (see webhook/results.py).
    await runs.create_run(run_id, event)

    # 1. Verify the AI service is reachable before doing any work.
    await runs.start_step(run_id, "clone")
    if not ai_reachable():
        logger.error(
            "run_id=%s AI service unreachable — skipping pipeline for %s [%s]",
            run_id, event.repo_name, event.branch,
        )
        await runs.finish_step(run_id, "clone", status="failed", error="AI service unreachable")
        await runs.patch_run(run_id, status="failed")
        return

    # 2. Clone the repo and extract README / file tree / changed-file contents.
    repo_context = build_repo_context(event, settings.github_token)
    logger.info("run_id=%s repo_type=%s cloned=%s", run_id, repo_context.repo_type, repo_context.cloned)
    await runs.finish_step(
        run_id, "clone",
        output=(
            f"repo_type={repo_context.repo_type} · cloned={repo_context.cloned} · "
            f"{len(event.changed_files)} changed file(s): {', '.join(event.changed_files[:8]) or '—'}"
        ),
    )

    try:
        # 2b. Skip regeneration if we've already produced tests for this exact diff.
        #     Saves the analyze + generate AI calls on reopened PRs / identical re-pushes.
        fingerprint = fingerprints.compute_fingerprint(event, repo_context)
        seen = await fingerprints.is_seen(event.repo_name, event.branch, fingerprint)
        if seen:
            logger.info(
                "run_id=%s diff fingerprint %s already generated (%s) — reusing committed tests, GitHub Actions will re-run them",
                run_id, fingerprint, seen.get("test_file", "?"),
            )
            note = f"identical diff already generated ({seen.get('test_file', '?')}) — reusing committed tests"
            for key in ("analyze", "generate", "push"):
                await runs.finish_step(run_id, key, status="skipped", output=note)
            await runs.start_step(run_id, "actions")  # committed tests still re-run in Actions
            return

        # 3. Analyze the change: decide whether to test and what kind of tests fit.
        await runs.start_step(run_id, "analyze")
        test_plan = await analyze_event(event, repo_context)
        logger.info(
            "run_id=%s should_test=%s test_kind=%s priority=%s reasoning=%s",
            run_id, test_plan.should_test, test_plan.test_kind,
            test_plan.priority, test_plan.reasoning[:100],
        )
        await runs.finish_step(
            run_id, "analyze",
            output=f"should_test={test_plan.should_test} · kind={test_plan.test_kind} · priority={test_plan.priority}",
        )
        await runs.patch_run(run_id, test_plan={
            "should_test": test_plan.should_test,
            "test_kind": test_plan.test_kind,
            "priority": test_plan.priority,
            "reasoning": test_plan.reasoning,
        })

        if not test_plan.should_test:
            logger.info("run_id=%s change not worth testing — pipeline complete", run_id)
            for key in ("generate", "push", "actions"):
                await runs.finish_step(run_id, key, status="skipped", output="change not worth testing")
            await runs.patch_run(run_id, status="completed")
            return

        # 4. Generate pytest/Playwright tests customized to the actual code change.
        await runs.start_step(run_id, "generate")
        generated_tests = await generate_tests(event, test_plan, repo_context)
        if not generated_tests:
            logger.error("run_id=%s test generation failed — aborting", run_id)
            await runs.finish_step(run_id, "generate", status="failed", error="test generation failed")
            await runs.patch_run(run_id, status="failed")
            return
        logger.info(
            "run_id=%s generated %d test(s) in %s",
            run_id, len(generated_tests.test_names), generated_tests.file_name,
        )
        await runs.finish_step(
            run_id, "generate",
            output=(
                f"{generated_tests.file_name}: {len(generated_tests.test_names)} test(s)"
                + (f" — {', '.join(generated_tests.test_names)}" if generated_tests.test_names else "")
            ),
        )
        await runs.patch_run(run_id, generated_tests={
            "file_name": generated_tests.file_name,
            "test_names": list(generated_tests.test_names),
            "triggered_by": list(generated_tests.triggered_by),
            "code": generated_tests.code,
        })

        # 5. Push the generated test file + the static ARIA workflow.
        #    The workflow is generic and committed once (idempotent push skips it when
        #    unchanged); GitHub Actions runs the tests and POSTs results to /webhook/results.
        await runs.start_step(run_id, "push")
        files_to_push = {
            f"testing/suites/generated/{generated_tests.file_name}": generated_tests.code,
            CONFTEST_PATH: CONFTEST_CONTENT,
            STATIC_WORKFLOW_PATH: STATIC_WORKFLOW_CONTENT,
        }
        commit_msg = f"chore(aria): generated tests for run {run_id} [{ARIA_COMMIT_MARKER}]"

        if not settings.github_token:
            logger.error("run_id=%s GITHUB_TOKEN not configured — cannot push to repo", run_id)
            await runs.finish_step(run_id, "push", status="failed", error="GITHUB_TOKEN not configured")
            await runs.patch_run(run_id, status="failed")
            return

        pushed = await push_files_to_branch(
            event.repo_name, event.branch, files_to_push,
            settings.github_token, commit_message=commit_msg,
        )
        if pushed:
            # Record the fingerprint so an identical future diff skips regeneration.
            await fingerprints.mark(
                event.repo_name, event.branch, fingerprint, generated_tests.file_name
            )
            logger.info(
                "run_id=%s pushed %d file(s) to %s@%s — GitHub Actions will run the tests",
                run_id, len(files_to_push), event.repo_name, event.branch,
            )
            await runs.finish_step(
                run_id, "push",
                output=f"pushed {len(files_to_push)} file(s): test + conftest + {STATIC_WORKFLOW_PATH}",
            )
            # Now hand off to GitHub Actions; the results callback finishes this step.
            await runs.start_step(run_id, "actions")
        else:
            logger.error(
                "run_id=%s push failed for %s@%s — verify GITHUB_TOKEN has contents:write permission",
                run_id, event.repo_name, event.branch,
            )
            await runs.finish_step(run_id, "push", status="failed", error="push failed — check GITHUB_TOKEN contents:write")
            await runs.patch_run(run_id, status="failed")
    except AIQuotaExceededError:
        await runs.patch_run(run_id, status="failed")
        raise
    except Exception:
        await runs.patch_run(run_id, status="failed")
        raise
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
