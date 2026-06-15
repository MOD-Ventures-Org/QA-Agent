"""
ARIA Happy & Sad Flow Tests
============================

Happy flows  — everything works end-to-end.
Sad flows    — one piece fails; the rest of the pipeline must survive.

Run with:
    cd C:\\Users\\User\\Desktop\\aria
    python -m pytest tests/test_flows.py -v
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


@contextlib.contextmanager
def _stub_run_tracking():
    """Stub the run-lifecycle writes + the Discord start ping so pipeline tests
    don't touch MongoDB or Discord."""
    with (
        patch("storage.runs.create_run", new_callable=AsyncMock),
        patch("storage.runs.start_step", new_callable=AsyncMock),
        patch("storage.runs.finish_step", new_callable=AsyncMock),
        patch("storage.runs.patch_run", new_callable=AsyncMock),
        patch("integrations.discord.post_run_started", new_callable=AsyncMock, return_value=""),
    ):
        yield

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sign(body: bytes, secret: str) -> str:
    """Produce the X-Hub-Signature-256 header value."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _push_payload(
    repo: str = "org/repo",
    branch: str = "main",
    files: tuple = ("src/api.py",),
) -> dict:
    return {
        "repository": {"full_name": repo},
        "ref": f"refs/heads/{branch}",
        "sender": {"login": "dev"},
        "commits": [
            {
                "message": "feat: update auth endpoint",
                "added": [],
                "modified": list(files),
                "removed": [],
            }
        ],
    }


def _pr_payload(repo: str = "org/repo", branch: str = "feature/login", merged: bool = True, action: str = "closed", base: str = "develop") -> dict:
    return {
        "repository": {"full_name": repo},
        "sender": {"login": "dev"},
        "action": action,
        "pull_request": {
            "head": {"ref": branch},
            "base": {"ref": base},
            "title": "Add login feature",
            "merged": merged,
        },
        "commits": [],
    }


def _post_webhook(client: TestClient, payload: dict, event: str, secret: str):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": _sign(body, secret),
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def webhook_secret() -> str:
    """Pull the real secret from .env so we sign correctly."""
    from config import settings
    return settings.webhook_secret


@pytest.fixture
def http_client():
    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# SECTION 1 — Webhook endpoint (HTTP layer)
# ---------------------------------------------------------------------------

class TestWebhookHappyFlow:
    """Valid requests must be accepted instantly; pipeline queued in background."""

    def test_health_check(self, http_client):
        r = http_client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_valid_push_event_accepted(self, http_client, webhook_secret):
        with patch("webhook.router._run_pipeline", new_callable=AsyncMock):
            r = _post_webhook(http_client, _push_payload(), "push", webhook_secret)
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"
        assert r.json()["event"] == "push"

    def test_valid_pull_request_event_accepted(self, http_client, webhook_secret):
        with patch("webhook.router._run_pipeline", new_callable=AsyncMock):
            r = _post_webhook(http_client, _pr_payload(), "pull_request", webhook_secret)
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

    def test_valid_release_event_accepted(self, http_client, webhook_secret):
        payload = {
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "dev"},
            "release": {"tag_name": "v2.0.0"},
            "commits": [],
        }
        with patch("webhook.router._run_pipeline", new_callable=AsyncMock):
            r = _post_webhook(http_client, payload, "release", webhook_secret)
        assert r.status_code == 200


class TestWebhookSadFlow:
    """Bad requests must be rejected before the pipeline is ever touched."""

    def test_missing_signature_returns_401(self, http_client):
        body = json.dumps(_push_payload()).encode()
        r = http_client.post(
            "/webhook/github",
            content=body,
            headers={"Content-Type": "application/json", "X-GitHub-Event": "push"},
        )
        assert r.status_code == 401

    def test_wrong_signature_returns_401(self, http_client):
        body = json.dumps(_push_payload()).encode()
        r = http_client.post(
            "/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": "sha256=deadbeef",
            },
        )
        assert r.status_code == 401

    def test_unsupported_event_type_returns_400(self, http_client, webhook_secret):
        body = json.dumps({"zen": "test"}).encode()
        r = http_client.post(
            "/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",  # not in SUPPORTED_EVENTS
                "X-Hub-Signature-256": _sign(body, webhook_secret),
            },
        )
        assert r.status_code == 400

    def test_tampered_body_rejected(self, http_client, webhook_secret):
        original = json.dumps(_push_payload()).encode()
        tampered = original + b"extra"
        r = http_client.post(
            "/webhook/github",
            content=tampered,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": _sign(original, webhook_secret),  # signed original
            },
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# SECTION 1b — Trigger gate (merge/main push + deployment only)
# ---------------------------------------------------------------------------

class TestWebhookTriggerGate:
    """Pipeline runs only on merged PRs, pushes to main/master, and deploy/release."""

    def test_push_to_feature_branch_skipped(self, http_client, webhook_secret):
        r = _post_webhook(http_client, _push_payload(branch="feature/x"), "push", webhook_secret)
        assert r.json()["status"] == "skipped"

    def test_push_to_main_accepted(self, http_client, webhook_secret):
        with patch("webhook.router._run_pipeline", new_callable=AsyncMock):
            r = _post_webhook(http_client, _push_payload(branch="main"), "push", webhook_secret)
        assert r.json()["status"] == "accepted"

    def test_unmerged_pr_skipped(self, http_client, webhook_secret):
        r = _post_webhook(http_client, _pr_payload(merged=False), "pull_request", webhook_secret)
        assert r.json()["status"] == "skipped"

    def test_merged_pr_accepted(self, http_client, webhook_secret):
        with patch("webhook.router._run_pipeline", new_callable=AsyncMock):
            r = _post_webhook(http_client, _pr_payload(merged=True, base="develop"), "pull_request", webhook_secret)
        assert r.json()["status"] == "accepted"

    def test_merged_pr_into_main_skipped_to_avoid_duplicate(self, http_client, webhook_secret):
        # Merging into main also fires a push-to-main event; process that one only.
        r = _post_webhook(http_client, _pr_payload(merged=True, base="main"), "pull_request", webhook_secret)
        assert r.json()["status"] == "skipped"

    def test_deployment_event_skipped(self, http_client, webhook_secret):
        # Plain "deployment" events are ignored — wait for "deployment_status" instead.
        payload = {
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "dev"},
            "deployment": {"ref": "main"},
            "commits": [],
        }
        r = _post_webhook(http_client, payload, "deployment", webhook_secret)
        assert r.json()["status"] == "skipped"

    def test_deployment_status_event_accepted(self, http_client, webhook_secret):
        payload = {
            "repository": {"full_name": "org/repo"},
            "sender": {"login": "dev"},
            "deployment": {"ref": "main"},
            "deployment_status": {"state": "success"},
            "commits": [],
        }
        with patch("webhook.router._run_pipeline", new_callable=AsyncMock):
            r = _post_webhook(http_client, payload, "deployment_status", webhook_secret)
        assert r.json()["status"] == "accepted"


class TestEventExtraction:
    """Author, branch, and commit must populate for deployment/release payloads."""

    def test_deployment_populates_author_branch_commit(self):
        from webhook.router import _extract_event
        payload = {
            "repository": {"full_name": "org/api"},
            "sender": {"login": "Haider-MOD"},
            "deployment": {"ref": "Staging", "description": "Worked on the Bugs & KYC form."},
        }
        event = _extract_event("deployment", payload)
        assert event.author == "Haider-MOD"
        assert event.branch == "Staging"
        assert event.commit_messages == ["Worked on the Bugs & KYC form."]

    def test_push_uses_head_commit_when_no_commits(self):
        from webhook.router import _extract_event
        payload = {
            "repository": {"full_name": "org/api"},
            "ref": "refs/heads/main",
            "sender": {"login": "dev"},
            "head_commit": {"message": "fix: patch login"},
        }
        event = _extract_event("push", payload)
        assert event.commit_messages == ["fix: patch login"]


class TestGracefulFailures:
    """When the AI service is unreachable, reports stay clean (no raw errno)."""

    def test_analyzer_network_error_reasoning_is_clean(self):
        from claude.analyzer import analyze_event
        from claude.repo_context import RepoContext
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/repo", branch="main", author="d",
            commit_messages=[], changed_files=[], diff_summary="d",
        )
        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.side_effect = OSError("[Errno 11001] getaddrinfo failed")
            plan = asyncio.run(analyze_event(event, RepoContext(repo_type="unknown")))

        assert "getaddrinfo" not in plan.reasoning
        assert "11001" not in plan.reasoning
        assert "AI service" in plan.reasoning

    def test_bug_summary_graceful_when_ai_unreachable(self):
        from claude.report_writer import write_bug_report
        from claude.analyzer import TestPlan
        from testing.result_parser import TestResult

        result = TestResult(total=2, passed=0, failed=2, failure_details=[
            {"name": "api/test_auth.py::test_login", "error": "boom", "traceback": ""},
            {"name": "api/test_auth.py::test_logout", "error": "boom", "traceback": ""},
        ])
        with patch("claude.report_writer.client") as mock_client:
            mock_client.messages.create.side_effect = OSError("[Errno 11001] getaddrinfo failed")
            bug_report = asyncio.run(write_bug_report(TestPlan(reasoning="r"), result))

        assert "getaddrinfo" not in bug_report.summary
        assert "2 automated test" in bug_report.summary
        assert "test_login" in bug_report.summary
        assert len(bug_report.items) == 2
        assert bug_report.items[0].test_name == "api/test_auth.py::test_login"


# ---------------------------------------------------------------------------
# SECTION 2 — AI Analyzer (claude/analyzer.py)
# ---------------------------------------------------------------------------

class TestAnalyzerHappyFlow:
    """Analyzer turns a GitHub event into a TestPlan."""

    def test_analyzer_returns_test_plan_from_valid_ai_response(self):
        from claude.analyzer import analyze_event
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push",
            repo_name="org/backend",
            branch="main",
            author="dev",
            commit_messages=["fix: update auth route"],
            changed_files=["api/auth.py"],
            diff_summary="1 commit(s) touching 1 file(s)",
        )

        ai_response = json.dumps({
            "reasoning": "Auth file changed — generate API tests for login",
            "should_test": True,
            "test_kind": "api",
            "pytest_keyword": "",
            "priority": "high",
            "focus_areas": ["authentication"],
            "affected_pages": ["/login"],
        })

        mock_response = MagicMock()
        mock_response.content = [SimpleNamespace(text=ai_response)]

        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            plan = asyncio.run(analyze_event(event))

        assert plan.should_test is True
        assert plan.test_kind == "api"
        assert plan.priority == "high"
        assert plan.focus_areas == ["authentication"]

    def test_analyzer_docs_only_change_sets_low_priority(self):
        from claude.analyzer import analyze_event
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push",
            repo_name="org/repo",
            branch="docs-update",
            author="dev",
            commit_messages=["docs: update README"],
            changed_files=["README.md", "docs/guide.md"],
            diff_summary="1 commit(s) touching 2 file(s)",
        )

        ai_response = json.dumps({
            "reasoning": "Only docs changed",
            "should_test": False,
            "test_kind": "mixed",
            "pytest_keyword": "",
            "priority": "low",
            "focus_areas": [],
            "affected_pages": [],
        })

        mock_response = MagicMock()
        mock_response.content = [SimpleNamespace(text=ai_response)]

        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            plan = asyncio.run(analyze_event(event))

        assert plan.priority == "low"
        assert plan.should_test is False


class TestAnalyzerSadFlow:
    """Analyzer must fall back to a testable plan when the AI response is unusable."""

    def test_fallback_when_ai_returns_invalid_json(self):
        from claude.analyzer import analyze_event
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/repo", branch="main",
            author="dev", commit_messages=[], changed_files=[],
            diff_summary="0 commit(s) touching 0 file(s)",
        )

        mock_response = MagicMock()
        mock_response.content = [SimpleNamespace(text="not valid json {{{")]

        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            plan = asyncio.run(analyze_event(event))

        # Unusable response -> assume the change is testable, let the generator decide.
        assert plan.should_test is True
        assert plan.priority == "medium"

    def test_fallback_when_ai_raises_exception(self):
        from claude.analyzer import analyze_event
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/repo", branch="main",
            author="dev", commit_messages=[], changed_files=[],
            diff_summary="0 commit(s) touching 0 file(s)",
        )

        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.side_effect = RuntimeError("API unreachable")
            plan = asyncio.run(analyze_event(event))

        assert plan.should_test is True

    def test_fallback_when_ai_returns_empty_response(self):
        from claude.analyzer import analyze_event
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/repo", branch="main",
            author="dev", commit_messages=[], changed_files=[],
            diff_summary="0 commit(s) touching 0 file(s)",
        )

        mock_response = MagicMock()
        mock_response.content = [SimpleNamespace(text="   ")]

        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            plan = asyncio.run(analyze_event(event))

        assert plan.should_test is True

    def test_quota_exceeded_propagates_instead_of_falling_back(self):
        from claude.analyzer import analyze_event
        from claude.client import AIQuotaExceededError
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/repo", branch="main",
            author="dev", commit_messages=[], changed_files=[],
            diff_summary="0 commit(s) touching 0 file(s)",
        )

        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.side_effect = AIQuotaExceededError("rate_limit_error: 429 Too Many Requests")
            with pytest.raises(AIQuotaExceededError):
                asyncio.run(analyze_event(event))


# ---------------------------------------------------------------------------
# SECTION 2b — test_kind inference, keyword passthrough, deployment + fallback
# ---------------------------------------------------------------------------

class TestPlanShape:
    """Analyzer maps a change to should_test + test_kind, inferring kind from repo type."""

    def _event(self, repo_name: str):
        from webhook.models import GitHubPushEvent

        return GitHubPushEvent(
            event_type="push", repo_name=repo_name, branch="main", author="dev",
            commit_messages=["chore: change"], changed_files=["src/thing.py"],
            diff_summary="1 commit(s) touching 1 file(s)",
        )

    def _plan_json(self, **fields):
        base = {
            "reasoning": "test", "should_test": True, "test_kind": "",
            "pytest_keyword": "", "priority": "medium",
            "focus_areas": [], "affected_pages": [],
        }
        base.update(fields)
        return json.dumps(base)

    def _analyze(self, repo_name, ai_json):
        from claude.analyzer import analyze_event
        from claude.repo_context import RepoContext, detect_repo_type

        resp = MagicMock()
        resp.content = [SimpleNamespace(text=ai_json)]
        ctx = RepoContext(repo_type=detect_repo_type(repo_name, []))
        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.return_value = resp
            return asyncio.run(analyze_event(self._event(repo_name), ctx))

    def test_explicit_test_kind_is_respected(self):
        plan = self._analyze("org/app", self._plan_json(test_kind="functional"))
        assert plan.should_test is True
        assert plan.test_kind == "functional"

    def test_blank_test_kind_inferred_from_backend(self):
        plan = self._analyze("org/backend-service", self._plan_json(test_kind=""))
        assert plan.test_kind == "api"

    def test_blank_test_kind_inferred_from_frontend(self):
        plan = self._analyze("org/frontend-web", self._plan_json(test_kind=""))
        assert plan.test_kind == "ui"

    def test_keyword_passthrough(self):
        plan = self._analyze("org/backend", self._plan_json(pytest_keyword="login or auth"))
        assert plan.pytest_keyword == "login or auth"

    def test_fallback_is_testable_for_backend(self):
        from claude.analyzer import analyze_event
        from claude.repo_context import RepoContext

        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.side_effect = RuntimeError("boom")
            plan = asyncio.run(analyze_event(self._event("org/backend"), RepoContext(repo_type="backend")))

        assert plan.should_test is True
        assert plan.test_kind == "api"   # inferred from backend repo type
        assert plan.priority == "medium"

    def test_deployment_status_event_is_testable(self):
        from claude.analyzer import analyze_event
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="deployment_status", repo_name="org/anything", branch="main",
            author="dev", commit_messages=[], changed_files=[], diff_summary="deploy",
        )
        plan = asyncio.run(analyze_event(event))
        assert plan.should_test is True
        assert plan.priority == "high"


class TestRepoContext:
    """Cloning context: repo-type detection, graceful degradation, cleanup."""

    def test_detect_backend_by_name(self):
        from claude.repo_context import detect_repo_type
        assert detect_repo_type("org/backend-svc", []) == "backend"

    def test_detect_frontend_by_file_signal(self):
        from claude.repo_context import detect_repo_type
        assert detect_repo_type("org/app", ["package.json"]) == "frontend"

    def test_detect_unknown_when_ambiguous(self):
        from claude.repo_context import detect_repo_type
        assert detect_repo_type("org/app", ["README.md"]) == "unknown"

    def test_cleanup_removes_temp_dir(self, tmp_path):
        from claude.repo_context import RepoContext
        d = tmp_path / "clone"
        d.mkdir()
        ctx = RepoContext(local_path=str(d), cloned=True)
        ctx.cleanup()
        assert not d.exists()
        assert ctx.local_path is None

    def test_build_degrades_when_clone_fails(self):
        from claude import repo_context
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/backend", branch="main", author="dev",
            commit_messages=[], changed_files=["api/x.py"], diff_summary="d",
        )
        with patch("claude.repo_context._clone", return_value=False):
            ctx = repo_context.build_repo_context(event, github_token="")
        assert ctx.cloned is False
        assert ctx.repo_type == "backend"
        assert ctx.local_path is None

    def test_build_reads_cloned_repo(self):
        from pathlib import Path
        from claude import repo_context
        from webhook.models import GitHubPushEvent

        def fake_clone(repo_name, branch, token, dest):
            root = Path(dest)
            root.mkdir(parents=True, exist_ok=True)
            (root / "README.md").write_text("# Hello world", encoding="utf-8")
            (root / "package.json").write_text("{}", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.js").write_text("console.log(1)", encoding="utf-8")
            return True

        event = GitHubPushEvent(
            event_type="push", repo_name="org/app", branch="main", author="dev",
            commit_messages=[], changed_files=["src/app.js"], diff_summary="d",
        )
        with patch("claude.repo_context._clone", side_effect=fake_clone):
            ctx = repo_context.build_repo_context(event, github_token="")
        try:
            assert ctx.cloned is True
            assert "Hello world" in ctx.readme
            assert "src/app.js" in ctx.file_tree
            assert "src/app.js" in ctx.changed_file_contents
            assert ctx.repo_type == "frontend"
        finally:
            ctx.cleanup()


class TestRunnerKeyword:
    """The runner threads a pytest -k expression through when the plan sets one."""

    def test_cmd_without_keyword_has_no_k(self):
        from pathlib import Path
        from testing.runner import _build_pytest_cmd
        cmd = _build_pytest_cmd(["a.py"], Path("r.json"), "")
        assert "-k" not in cmd
        assert "a.py" in cmd

    def test_cmd_with_keyword_adds_k(self):
        from pathlib import Path
        from testing.runner import _build_pytest_cmd
        cmd = _build_pytest_cmd(["a.py"], Path("r.json"), "login or auth")
        assert "-k" in cmd
        assert cmd[cmd.index("-k") + 1] == "login or auth"


class TestManualTestCases:
    """Plain-English manual test cases for human QA: generation + delivery."""

    def _event(self):
        from webhook.models import GitHubPushEvent
        return GitHubPushEvent(
            event_type="push", repo_name="org/app", branch="main", author="alice",
            commit_messages=["feat: add password reset"], changed_files=["src/reset.py"],
            diff_summary="d",
        )

    def test_generate_manual_tests_parses_cases(self):
        from claude.manual_tests import generate_manual_tests

        ai = json.dumps({"cases": [
            {"title": "Reset with valid email", "steps": ["Open /reset", "Enter email", "Submit"],
             "expected": "Reset email is sent"},
            {"title": "Reset with unknown email", "steps": ["Open /reset", "Enter unknown email"],
             "expected": "Friendly error shown"},
        ]})
        resp = MagicMock()
        resp.content = [SimpleNamespace(text=ai)]
        with patch("claude.manual_tests.client") as mock_client:
            mock_client.messages.create.return_value = resp
            plan = asyncio.run(generate_manual_tests(self._event()))

        assert len(plan.cases) == 2
        assert plan.cases[0].title == "Reset with valid email"
        assert plan.cases[0].steps[0] == "Open /reset"
        assert "sent" in plan.cases[0].expected

    def test_generate_manual_tests_strips_code_fences(self):
        from claude.manual_tests import generate_manual_tests

        ai = '```json\n{"cases": [{"title": "T", "steps": ["s"], "expected": "e"}]}\n```'
        resp = MagicMock()
        resp.content = [SimpleNamespace(text=ai)]
        with patch("claude.manual_tests.client") as mock_client:
            mock_client.messages.create.return_value = resp
            plan = asyncio.run(generate_manual_tests(self._event()))

        assert len(plan.cases) == 1

    def test_generate_manual_tests_empty_on_failure(self):
        from claude.manual_tests import generate_manual_tests

        with patch("claude.manual_tests.client") as mock_client:
            mock_client.messages.create.side_effect = RuntimeError("boom")
            plan = asyncio.run(generate_manual_tests(self._event()))

        assert plan.cases == []

    def test_discord_manual_embed_lists_cases(self):
        from integrations.discord import _build_manual_tests_embed
        from claude.manual_tests import ManualTestPlan, ManualTestCase

        plan = ManualTestPlan(cases=[ManualTestCase(title="Login", steps=["go", "type"], expected="ok")])
        embed = _build_manual_tests_embed(plan)
        assert "Manual Test Cases" in embed["title"]
        assert "Login" in embed["description"]

    def test_clickup_manual_markdown_is_checklist(self):
        from integrations.clickup import _manual_cases_markdown
        from claude.manual_tests import ManualTestPlan, ManualTestCase

        plan = ManualTestPlan(cases=[ManualTestCase(title="Login", steps=["go", "type"], expected="dashboard")])
        md = _manual_cases_markdown(plan)
        assert "- [ ]" in md
        assert "Login" in md
        assert "dashboard" in md


class TestBugReportPlainEnglish:
    """ClickUp tickets for failing generated tests are written in plain English."""

    def _failures(self):
        return [{
            "name": "api/test_auth.py::test_login_with_expired_token",
            "error": "AssertionError: 401 != 200",
            "traceback": "Traceback (most recent call last): ...",
        }]

    def test_write_bug_report_parses_summary_and_items(self):
        from claude.report_writer import write_bug_report
        from claude.analyzer import TestPlan
        from testing.result_parser import TestResult

        ai = json.dumps({
            "summary": "Login is broken for users with expired tokens.",
            "items": [
                {
                    "test_name": "api/test_auth.py::test_login_with_expired_token",
                    "title": "Login fails for expired tokens",
                    "description": "Users with an expired session cannot log back in and see an error instead.",
                }
            ],
        })
        resp = MagicMock()
        resp.content = [SimpleNamespace(text=ai)]
        result = TestResult(total=1, passed=0, failed=1, failure_details=self._failures())
        with patch("claude.report_writer.client") as mock_client:
            mock_client.messages.create.return_value = resp
            bug_report = asyncio.run(write_bug_report(TestPlan(reasoning="r"), result))

        assert "expired tokens" in bug_report.summary
        assert len(bug_report.items) == 1
        item = bug_report.items[0]
        assert item.test_name == "api/test_auth.py::test_login_with_expired_token"
        assert item.title == "Login fails for expired tokens"
        assert "session" in item.description

    def test_plain_title_fallback_from_test_name(self):
        from claude.report_writer import plain_title

        assert plain_title("api/test_auth.py::test_login_with_expired_token") == "Login with expired token is broken"

    def test_file_bug_tickets_uses_plain_english_title_and_description(self):
        from config import settings
        from integrations.clickup import file_bug_tickets
        from claude.report_writer import BugReport, BugReportItem
        from claude.analyzer import TestPlan
        from testing.result_parser import TestResult

        result = TestResult(total=1, passed=0, failed=1, failure_details=self._failures())
        bug_report = BugReport(
            summary="Login is broken for users with expired tokens.",
            items=[BugReportItem(
                test_name="api/test_auth.py::test_login_with_expired_token",
                title="Login fails for expired tokens",
                description="Users with an expired session cannot log back in.",
            )],
        )

        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "task-1"}

        class FakeAsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, json, headers):
                captured["payload"] = json
                return FakeResponse()

        with (
            patch.object(settings, "clickup_enabled", True),
            patch.object(settings, "clickup_api_token", "tok"),
            patch.object(settings, "clickup_list_id", "list1"),
            patch("integrations.clickup.httpx.AsyncClient", FakeAsyncClient),
        ):
            ids = asyncio.run(file_bug_tickets("run1", _make_event(), TestPlan(priority="high"), result, bug_report))

        assert ids == ["task-1"]
        payload = captured["payload"]
        assert payload["name"] == "[ARIA] 1 failing test(s) — " + _make_event().repo_name + "/" + _make_event().branch
        assert "test_login_with_expired_token" not in payload["description"]
        assert "401" not in payload["description"]
        assert "Login fails for expired tokens" in payload["description"]
        assert "expired session" in payload["description"]

    def test_file_bug_tickets_consolidates_all_failures_into_one_ticket(self):
        from config import settings
        from integrations.clickup import file_bug_tickets
        from claude.report_writer import BugReport, BugReportItem
        from claude.analyzer import TestPlan
        from testing.result_parser import TestResult

        failures = [
            {"name": "api/test_auth.py::test_login_with_expired_token", "error": "AssertionError: 401 != 200"},
            {"name": "api/test_signup.py::test_signup_with_duplicate_email", "error": "AssertionError: 500 != 201"},
        ]
        result = TestResult(total=2, passed=0, failed=2, failure_details=failures)
        bug_report = BugReport(
            summary="Two checks failed.",
            items=[
                BugReportItem(
                    test_name="api/test_auth.py::test_login_with_expired_token",
                    title="Login fails for expired tokens",
                    description="Users with an expired session cannot log back in.",
                ),
                BugReportItem(
                    test_name="api/test_signup.py::test_signup_with_duplicate_email",
                    title="Signup with a duplicate email crashes",
                    description="Signing up with an email already in use returns a server error.",
                ),
            ],
        )

        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "task-consolidated"}

        class FakeAsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, json, headers):
                captured["payload"] = json
                return FakeResponse()

        with (
            patch.object(settings, "clickup_enabled", True),
            patch.object(settings, "clickup_api_token", "tok"),
            patch.object(settings, "clickup_list_id", "list1"),
            patch("integrations.clickup.httpx.AsyncClient", FakeAsyncClient),
        ):
            ids = asyncio.run(file_bug_tickets("run1", _make_event(), TestPlan(priority="high"), result, bug_report))

        # One ticket total, covering both failures.
        assert ids == ["task-consolidated"]
        payload = captured["payload"]
        assert "2 failing test(s)" in payload["name"]
        assert "Login fails for expired tokens" in payload["description"]
        assert "Signup with a duplicate email crashes" in payload["description"]


class TestDiscordReportFields:
    """The Discord report surfaces repo, branch, author, commit, and event."""

    def test_embed_includes_push_context(self):
        from integrations.discord import _build_embed
        from claude.analyzer import TestPlan
        from testing.result_parser import TestResult
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/api", branch="main", author="bob",
            commit_messages=["fix: patch login bug"], changed_files=["api/login.py"],
            diff_summary="d",
        )
        embed = _build_embed("run123", event, TestPlan(reasoning="r"), TestResult(), "")
        field_names = {f["name"] for f in embed["fields"]}
        values = {f["name"]: f["value"] for f in embed["fields"]}
        assert {"Repository", "Branch", "Event", "Pushed by", "Commit"} <= field_names
        assert values["Repository"] == "org/api"
        assert values["Pushed by"] == "bob"
        assert "patch login bug" in values["Commit"]

    def test_evaluation_is_merged_into_single_report(self):
        from integrations.discord import _build_embed
        from claude.analyzer import TestPlan
        from claude.evaluator import ProductEvaluation
        from testing.result_parser import TestResult
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/api", branch="main", author="bob",
            commit_messages=["x"], changed_files=[], diff_summary="d",
        )
        evaluation = ProductEvaluation(
            quality_score=88, grade="B", summary="Users can log in but reset is risky.",
            strengths=["Login works"], risks=["Password reset may fail"], recommendation="ship with caution",
        )
        embed = _build_embed("r1", event, TestPlan(reasoning="r"), TestResult(), "", evaluation)
        names = {f["name"] for f in embed["fields"]}
        # Evaluation lives inside the single report embed (no separate embed)
        assert "📊 Product Quality" in names
        assert "Assessment" in names


class TestProductEvaluationReadsCode:
    """The product evaluation prompt is grounded in the README and changed code."""

    def test_evaluator_prompt_includes_readme_and_code(self):
        from claude.prompts import evaluator_user_prompt
        from claude.analyzer import TestPlan
        from claude.repo_context import RepoContext
        from testing.result_parser import TestResult
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/app", branch="main", author="dev",
            commit_messages=["feat: KYC"], changed_files=["src/pay.py"], diff_summary="d",
        )
        ctx = RepoContext(
            repo_type="backend",
            readme="This app processes KYC payments for customers.",
            changed_file_contents={"src/pay.py": "def charge(card):\n    return True"},
        )
        prompt = evaluator_user_prompt(event, TestPlan(), TestResult(total=1, passed=1), ctx)

        assert "KYC payments" in prompt
        assert "src/pay.py" in prompt
        assert "def charge" in prompt

    def test_evaluator_prompt_without_context_still_works(self):
        from claude.prompts import evaluator_user_prompt
        from claude.analyzer import TestPlan
        from testing.result_parser import TestResult
        from webhook.models import GitHubPushEvent

        event = GitHubPushEvent(
            event_type="push", repo_name="org/app", branch="main", author="dev",
            commit_messages=[], changed_files=[], diff_summary="d",
        )
        prompt = evaluator_user_prompt(event, TestPlan(), TestResult(total=1, passed=1))
        assert "Return exactly this JSON shape" in prompt


# ---------------------------------------------------------------------------
# SECTION 3 — Result Parser (testing/result_parser.py)
# ---------------------------------------------------------------------------

class TestResultParserHappyFlow:
    """Parser turns a pytest JSON report into a TestResult."""

    def test_parses_all_passing_report(self):
        from testing.result_parser import parse_pytest_json

        report = {
            "duration": 12.3,
            "summary": {"total": 3, "passed": 3, "failed": 0, "error": 0},
            "tests": [
                {"nodeid": "suite/test_one.py::test_a", "outcome": "passed"},
                {"nodeid": "suite/test_one.py::test_b", "outcome": "passed"},
                {"nodeid": "suite/test_two.py::test_c", "outcome": "passed"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(report, f)
            path = f.name

        try:
            result = parse_pytest_json(path)
        finally:
            os.unlink(path)

        assert result.total == 3
        assert result.passed == 3
        assert result.failed == 0
        assert result.duration == 12.3
        assert result.failure_details == []

    def test_parses_mixed_pass_fail_report(self):
        from testing.result_parser import parse_pytest_json

        report = {
            "duration": 5.0,
            "summary": {"total": 4, "passed": 2, "failed": 2, "error": 0},
            "tests": [
                {"nodeid": "api/test_auth.py::test_login", "outcome": "passed"},
                {"nodeid": "api/test_auth.py::test_logout", "outcome": "passed"},
                {
                    "nodeid": "api/test_auth.py::test_refresh",
                    "outcome": "failed",
                    "call": {
                        "crash": {"message": "AssertionError: 401 != 200"},
                        "longrepr": "AssertionError: expected 200",
                    },
                },
                {
                    "nodeid": "api/test_auth.py::test_expired",
                    "outcome": "failed",
                    "call": {
                        "crash": {"message": "TimeoutError"},
                        "longrepr": "TimeoutError after 30s",
                    },
                },
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(report, f)
            path = f.name

        try:
            result = parse_pytest_json(path)
        finally:
            os.unlink(path)

        assert result.passed == 2
        assert result.failed == 2
        assert len(result.failure_details) == 2
        assert result.failure_details[0]["name"] == "api/test_auth.py::test_refresh"


class TestResultParserSadFlow:
    """Parser must return an error result when the report file is missing or corrupt."""

    def test_missing_report_file_returns_error(self):
        from testing.result_parser import parse_pytest_json

        result = parse_pytest_json("/tmp/nonexistent_aria_report_xyz.json")
        assert result.errors == 1
        assert len(result.failure_details) == 1
        assert "not found" in result.failure_details[0]["error"].lower()

    def test_empty_tests_list_returns_zero_counts(self):
        from testing.result_parser import parse_pytest_json

        report = {"duration": 0.1, "summary": {}, "tests": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(report, f)
            path = f.name

        try:
            result = parse_pytest_json(path)
        finally:
            os.unlink(path)

        assert result.total == 0
        assert result.passed == 0
        assert result.failed == 0


# ---------------------------------------------------------------------------
# SECTION 4 — Full Pipeline (webhook/router.py _run_pipeline)
# ---------------------------------------------------------------------------

def _make_event():
    from webhook.models import GitHubPushEvent
    return GitHubPushEvent(
        event_type="push",
        repo_name="org/repo",
        branch="main",
        author="dev",
        commit_messages=["fix: patch auth bug"],
        changed_files=["api/auth.py"],
        diff_summary="1 commit(s) touching 1 file(s)",
    )


def _passing_test_result():
    from testing.result_parser import TestResult
    return TestResult(total=5, passed=5, failed=0, errors=0, duration=8.2)


def _failing_test_result():
    from testing.result_parser import TestResult
    return TestResult(
        total=5,
        passed=3,
        failed=2,
        errors=0,
        duration=10.0,
        failure_details=[
            {"name": "api/test_auth.py::test_login", "error": "401 != 200", "traceback": ""},
            {"name": "api/test_auth.py::test_logout", "error": "Connection refused", "traceback": ""},
        ],
    )


def _test_plan():
    from claude.analyzer import TestPlan
    return TestPlan(
        reasoning="Auth file changed",
        should_test=True,
        test_kind="api",
        priority="high",
    )


def _evaluation():
    from claude.evaluator import ProductEvaluation
    return ProductEvaluation(
        quality_score=95,
        grade="A",
        summary="All tests pass.",
        recommendation="ship",
    )


def _dummy_repo_context():
    from claude.repo_context import RepoContext
    return RepoContext(repo_type="backend", cloned=False)


class TestPipelineHappyFlow:
    """All tests pass — no bug report, no ClickUp tickets, Discord gets a green embed."""

    @pytest.fixture(autouse=True)
    def _stub_repo_clone(self):
        # Prevent the pipeline from attempting a real git clone or AI call during tests,
        # and treat the AI service as reachable. Run-tracking writes are stubbed too.
        from claude.manual_tests import ManualTestPlan
        with (
            patch("claude.analyzer.ai_reachable", return_value=True),
            patch("claude.repo_context.build_repo_context", return_value=_dummy_repo_context()),
            patch("claude.manual_tests.generate_manual_tests", new_callable=AsyncMock, return_value=ManualTestPlan()),
            _stub_run_tracking(),
        ):
            yield

    def test_pipeline_completes_when_all_tests_pass(self):
        from webhook.router import _run_pipeline

        with (
            patch("claude.analyzer.analyze_event", new_callable=AsyncMock, return_value=_test_plan()),
            patch("claude.test_generator.generate_tests", new_callable=AsyncMock),
            patch("testing.runner.run_tests", new_callable=AsyncMock, return_value=_passing_test_result()),
            patch("testing.regression_watcher.check_regression", new_callable=AsyncMock, return_value=_passing_test_result()),
            patch("claude.evaluator.evaluate_product", new_callable=AsyncMock, return_value=_evaluation()),
            patch("storage.mongo.save_test_run", new_callable=AsyncMock, return_value="abc12345"),
            patch("claude.report_writer.write_bug_report", new_callable=AsyncMock) as mock_bug,
            patch("integrations.clickup.file_bug_tickets", new_callable=AsyncMock) as mock_clickup,
            patch("integrations.clickup.file_manual_test_ticket", new_callable=AsyncMock) as mock_manual_ticket,
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, return_value="discord-msg-id"),
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock),
            patch("storage.mongo.save_manual_tests", new_callable=AsyncMock),
        ):
            asyncio.run(_run_pipeline(_make_event()))

        # No failures → bug report, bug ticket, and manual ticket must NOT be created
        mock_bug.assert_not_called()
        mock_clickup.assert_not_called()
        mock_manual_ticket.assert_not_called()

    def test_pipeline_saves_run_to_mongo(self):
        from webhook.router import _run_pipeline

        with (
            patch("claude.analyzer.analyze_event", new_callable=AsyncMock, return_value=_test_plan()),
            patch("claude.test_generator.generate_tests", new_callable=AsyncMock),
            patch("testing.runner.run_tests", new_callable=AsyncMock, return_value=_passing_test_result()),
            patch("testing.regression_watcher.check_regression", new_callable=AsyncMock, return_value=_passing_test_result()),
            patch("claude.evaluator.evaluate_product", new_callable=AsyncMock, return_value=_evaluation()),
            patch("storage.mongo.save_test_run", new_callable=AsyncMock, return_value="abc12345") as mock_save,
            patch("claude.report_writer.write_bug_report", new_callable=AsyncMock),
            patch("integrations.clickup.file_bug_tickets", new_callable=AsyncMock),
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, return_value=""),
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock),
        ):
            asyncio.run(_run_pipeline(_make_event()))

        mock_save.assert_called_once()


class TestPipelineSadFlow:
    """Failures in one stage must not crash the whole pipeline."""

    @pytest.fixture(autouse=True)
    def _stub_repo_clone(self):
        from claude.manual_tests import ManualTestPlan
        with (
            patch("claude.analyzer.ai_reachable", return_value=True),
            patch("claude.repo_context.build_repo_context", return_value=_dummy_repo_context()),
            patch("claude.manual_tests.generate_manual_tests", new_callable=AsyncMock, return_value=ManualTestPlan()),
            _stub_run_tracking(),
        ):
            yield

    def test_creates_bug_report_and_clickup_when_tests_fail(self):
        # Regular failures should still generate a bug summary and ClickUp tickets, with Discord reporting the result.
        from webhook.router import _run_pipeline

        with (
            patch("claude.analyzer.analyze_event", new_callable=AsyncMock, return_value=_test_plan()),
            patch("claude.test_generator.generate_tests", new_callable=AsyncMock),
            patch("testing.runner.run_tests", new_callable=AsyncMock, return_value=_failing_test_result()),
            patch("testing.regression_watcher.check_regression", new_callable=AsyncMock, return_value=_failing_test_result()),
            patch("claude.evaluator.evaluate_product", new_callable=AsyncMock, return_value=_evaluation()),
            patch("storage.mongo.save_test_run", new_callable=AsyncMock, return_value="abc12345"),
            patch("claude.report_writer.write_bug_report", new_callable=AsyncMock) as mock_bug,
            patch("integrations.clickup.file_bug_tickets", new_callable=AsyncMock) as mock_cu,
            patch("integrations.clickup.file_manual_test_ticket", new_callable=AsyncMock, return_value="cu-manual") as mock_manual_ticket,
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, return_value="") as mock_discord,
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock) as mock_save_bug,
            patch("storage.mongo.save_manual_tests", new_callable=AsyncMock),
        ):
            asyncio.run(_run_pipeline(_make_event()))

        mock_bug.assert_called_once()
        mock_cu.assert_called_once()
        mock_manual_ticket.assert_called_once()  # bugs present → manual ticket filed
        mock_discord.assert_called_once()
        mock_save_bug.assert_called_once()

    def test_pipeline_survives_discord_failure(self):
        # _run_pipeline_safe is the real background task — it catches all exceptions
        # so the webhook never crashes even when Discord is down.
        from webhook.router import _run_pipeline_safe

        with (
            patch("claude.analyzer.analyze_event", new_callable=AsyncMock, return_value=_test_plan()),
            patch("claude.test_generator.generate_tests", new_callable=AsyncMock),
            patch("testing.runner.run_tests", new_callable=AsyncMock, return_value=_passing_test_result()),
            patch("testing.regression_watcher.check_regression", new_callable=AsyncMock, return_value=_passing_test_result()),
            patch("claude.evaluator.evaluate_product", new_callable=AsyncMock, return_value=_evaluation()),
            patch("storage.mongo.save_test_run", new_callable=AsyncMock, return_value="abc12345"),
            patch("claude.report_writer.write_bug_report", new_callable=AsyncMock),
            patch("integrations.clickup.file_bug_tickets", new_callable=AsyncMock),
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, side_effect=Exception("Discord 500")),
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock),
        ):
            # Must NOT raise — safe wrapper absorbs the Discord exception
            asyncio.run(_run_pipeline_safe(_make_event()))

    def test_pipeline_survives_mongodb_failure(self):
        from webhook.router import _run_pipeline_safe

        with (
            patch("claude.analyzer.analyze_event", new_callable=AsyncMock, return_value=_test_plan()),
            patch("claude.test_generator.generate_tests", new_callable=AsyncMock),
            patch("testing.runner.run_tests", new_callable=AsyncMock, return_value=_passing_test_result()),
            patch("testing.regression_watcher.check_regression", new_callable=AsyncMock, return_value=_passing_test_result()),
            patch("claude.evaluator.evaluate_product", new_callable=AsyncMock, return_value=_evaluation()),
            patch("storage.mongo.save_test_run", new_callable=AsyncMock, side_effect=Exception("Mongo down")),
            patch("claude.report_writer.write_bug_report", new_callable=AsyncMock),
            patch("integrations.clickup.file_bug_tickets", new_callable=AsyncMock) as mock_clickup,
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, return_value="") as mock_discord,
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock),
        ):
            # Must NOT raise — safe wrapper absorbs the MongoDB exception and still posts to Discord
            asyncio.run(_run_pipeline_safe(_make_event()))

        mock_discord.assert_called_once()
        mock_clickup.assert_not_called()

    def test_discord_receives_report_with_failure_count(self):
        # Discord is always called; it receives the real failure count even with a bug summary.
        from webhook.router import _run_pipeline

        result = _failing_test_result()

        with (
            patch("claude.analyzer.analyze_event", new_callable=AsyncMock, return_value=_test_plan()),
            patch("claude.test_generator.generate_tests", new_callable=AsyncMock),
            patch("testing.runner.run_tests", new_callable=AsyncMock, return_value=result),
            patch("testing.regression_watcher.check_regression", new_callable=AsyncMock, return_value=result),
            patch("claude.evaluator.evaluate_product", new_callable=AsyncMock, return_value=_evaluation()),
            patch("storage.mongo.save_test_run", new_callable=AsyncMock, return_value="abc12345"),
            patch("claude.report_writer.write_bug_report", new_callable=AsyncMock),
            patch("integrations.clickup.file_bug_tickets", new_callable=AsyncMock),
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, return_value="msg-123") as mock_discord,
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock),
        ):
            asyncio.run(_run_pipeline(_make_event()))

        mock_discord.assert_called_once()
        # Verify the result passed to Discord has the correct failure count
        call_args = mock_discord.call_args
        passed_result = call_args.args[3]  # (run_id, event, test_plan, result, ...)
        assert passed_result.failed == 2

    def test_errored_run_reports_to_discord_only(self):
        # errors > 0 (tests couldn't run reliably): Discord report only —
        # no MongoDB save and no ClickUp tickets.
        from webhook.router import _run_pipeline
        from claude.report_writer import BugReport
        from testing.result_parser import TestResult

        errored = TestResult(
            total=1, passed=0, failed=0, errors=1,
            failure_details=[{"name": "timeout", "error": "pytest timed out", "traceback": ""}],
        )
        with (
            patch("claude.analyzer.analyze_event", new_callable=AsyncMock, return_value=_test_plan()),
            patch("claude.test_generator.generate_tests", new_callable=AsyncMock),
            patch("testing.runner.run_tests", new_callable=AsyncMock, return_value=errored),
            patch("testing.regression_watcher.check_regression", new_callable=AsyncMock, return_value=errored),
            patch("claude.evaluator.evaluate_product", new_callable=AsyncMock, return_value=_evaluation()),
            patch("claude.report_writer.write_bug_report", new_callable=AsyncMock, return_value=BugReport(summary="error summary")),
            patch("storage.mongo.save_test_run", new_callable=AsyncMock) as mock_save,
            patch("storage.mongo.save_manual_tests", new_callable=AsyncMock) as mock_save_manual,
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock) as mock_save_bug,
            patch("integrations.clickup.file_bug_tickets", new_callable=AsyncMock) as mock_bug_ticket,
            patch("integrations.clickup.file_manual_test_ticket", new_callable=AsyncMock) as mock_manual_ticket,
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, return_value="msg") as mock_report,
        ):
            asyncio.run(_run_pipeline(_make_event()))

        mock_report.assert_called_once()       # Discord report IS shown
        mock_save.assert_not_called()          # NOT saved to MongoDB
        mock_save_manual.assert_not_called()
        mock_save_bug.assert_not_called()
        mock_bug_ticket.assert_not_called()    # NO ClickUp tickets
        mock_manual_ticket.assert_not_called()


class TestPipelineAIUnavailable:
    """When the AI service is unreachable, the pipeline does nothing except post one alert."""

    def test_skips_all_work_and_posts_single_alert(self):
        from webhook.router import _run_pipeline

        with (
            _stub_run_tracking(),
            patch("claude.analyzer.ai_reachable", return_value=False),
            patch("claude.repo_context.build_repo_context") as mock_clone,
            patch("testing.runner.run_tests", new_callable=AsyncMock) as mock_run,
            patch("claude.report_writer.write_bug_report", new_callable=AsyncMock) as mock_bug,
            patch("integrations.clickup.file_bug_tickets", new_callable=AsyncMock) as mock_bug_ticket,
            patch("integrations.clickup.file_manual_test_ticket", new_callable=AsyncMock) as mock_manual_ticket,
            patch("storage.mongo.save_test_run", new_callable=AsyncMock) as mock_save,
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock) as mock_report,
            patch("integrations.discord.post_ai_unavailable_report", new_callable=AsyncMock, return_value="alert-id") as mock_alert,
        ):
            asyncio.run(_run_pipeline(_make_event()))

        mock_alert.assert_called_once()        # one concise alert only
        mock_clone.assert_not_called()         # no repo clone
        mock_run.assert_not_called()           # no tests
        mock_bug.assert_not_called()           # no bug summary
        mock_bug_ticket.assert_not_called()    # no bug tickets
        mock_manual_ticket.assert_not_called()  # no manual ticket
        mock_save.assert_not_called()          # nothing saved
        mock_report.assert_not_called()        # no full report


class TestPipelineAIQuotaExceeded:
    """When an AI provider reports a token/usage/quota limit error mid-run, the
    run is marked failed (logs + dashboard) and a focused Discord alert is sent."""

    def test_quota_error_marks_run_failed_and_alerts_discord(self):
        from webhook.router import _run_pipeline_safe
        from claude.client import AIQuotaExceededError

        with (
            patch("storage.runs.create_run", new_callable=AsyncMock),
            patch("storage.runs.start_step", new_callable=AsyncMock),
            patch("storage.runs.finish_step", new_callable=AsyncMock) as mock_finish,
            patch("storage.runs.patch_run", new_callable=AsyncMock) as mock_patch,
            patch("storage.runs.get_run", new_callable=AsyncMock, return_value={"steps": [{"key": "analyze", "status": "running"}]}),
            patch("integrations.discord.post_run_started", new_callable=AsyncMock, return_value=""),
            patch("integrations.discord.post_ai_quota_exceeded_report", new_callable=AsyncMock, return_value="alert-id") as mock_alert,
            patch("claude.analyzer.ai_reachable", return_value=True),
            patch("claude.repo_context.build_repo_context", return_value=_dummy_repo_context()),
            patch("claude.analyzer.analyze_event", new_callable=AsyncMock, side_effect=AIQuotaExceededError("rate_limit_error: 429 Too Many Requests")),
        ):
            # Must NOT raise — the safe wrapper catches AIQuotaExceededError.
            asyncio.run(_run_pipeline_safe(_make_event()))

        mock_alert.assert_called_once()

        failed_steps = [c for c in mock_finish.call_args_list if c.args[1] == "analyze" and c.kwargs.get("status") == "failed"]
        assert failed_steps
        assert "quota" in failed_steps[-1].kwargs["error"].lower()

        failed_patches = [c for c in mock_patch.call_args_list if c.kwargs.get("status") == "failed"]
        assert failed_patches


# ---------------------------------------------------------------------------
# SECTION 5 — DualAIClient (claude/client.py)
# ---------------------------------------------------------------------------

class TestDualAIClientHappyFlow:

    def test_kimi_used_first_by_default(self):
        from claude.client import DualAIClient

        c = DualAIClient(anthropic_api_key="ant-key", kimi_api_key="kimi-key")
        order = c._provider_order("kimi-1.0")
        assert order[0] == "kimi"

    def test_falls_back_to_claude_when_kimi_fails(self):
        from claude.client import DualAIClient, DualAIResponse

        c = DualAIClient(anthropic_api_key="ant-key", kimi_api_key="kimi-key")

        with (
            patch.object(c, "_generate_kimi", side_effect=RuntimeError("Kimi down")),
            patch.object(c, "_generate_claude", return_value="Claude answer"),
        ):
            response = c.create(model="kimi-1.0", system="sys", messages=[{"role": "user", "content": "hi"}])

        assert response.content[0].text == "Claude answer"

    def test_kimi_succeeds_on_first_try(self):
        from claude.client import DualAIClient

        c = DualAIClient(anthropic_api_key="ant-key", kimi_api_key="kimi-key")

        with (
            patch.object(c, "_generate_kimi", return_value="Kimi answer"),
            patch.object(c, "_generate_claude") as mock_claude,
        ):
            response = c.create(model="kimi-1.0", system="sys", messages=[{"role": "user", "content": "hi"}])

        assert response.content[0].text == "Kimi answer"
        mock_claude.assert_not_called()


class TestDualAIClientSadFlow:

    def test_raises_when_both_providers_fail(self):
        from claude.client import DualAIClient

        c = DualAIClient(anthropic_api_key="ant-key", kimi_api_key="kimi-key")

        with (
            patch.object(c, "_generate_kimi", side_effect=RuntimeError("Kimi down")),
            patch.object(c, "_generate_claude", side_effect=RuntimeError("Claude down")),
        ):
            with pytest.raises(RuntimeError):
                c.create(model="kimi-1.0", system="sys", messages=[{"role": "user", "content": "hi"}])

    def test_raises_when_no_api_keys_configured(self):
        from claude.client import DualAIClient

        c = DualAIClient()  # no keys

        with pytest.raises(Exception):
            c.create(model="kimi-1.0", system="sys", messages=[{"role": "user", "content": "hi"}])

    def test_billing_error_is_wrapped_with_clear_message(self):
        from claude.client import DualAIClient

        c = DualAIClient(anthropic_api_key="ant-key", kimi_api_key="kimi-key")
        billing_exc = Exception("403 Forbidden: billing account inactive")
        wrapped = c._wrap_kimi_error(billing_exc)

        assert "billing" in str(wrapped).lower()

    def test_raises_quota_exceeded_when_all_providers_hit_rate_or_usage_limits(self):
        from claude.client import AIQuotaExceededError, DualAIClient

        c = DualAIClient(anthropic_api_key="ant-key", kimi_api_key="kimi-key")

        with (
            patch.object(c, "_generate_kimi", side_effect=RuntimeError("429 rate_limit_exceeded: too many requests")),
            patch.object(c, "_generate_claude", side_effect=RuntimeError("rate_limit_error: usage limit exceeded for this month")),
        ):
            with pytest.raises(AIQuotaExceededError):
                c.create(model="kimi-1.0", system="sys", messages=[{"role": "user", "content": "hi"}])

    def test_quota_error_not_masked_by_unconfigured_fallback_provider(self):
        from claude.client import AIQuotaExceededError, DualAIClient

        # Only Anthropic is configured. "kimi" is still tried as a fallback and
        # immediately fails with "not configured" — that must not hide the real
        # rate-limit error Claude returned first.
        c = DualAIClient(anthropic_api_key="ant-key", kimi_api_key="")

        with patch.object(c, "_generate_claude", side_effect=RuntimeError("rate_limit_error: 429 Too Many Requests")):
            with pytest.raises(AIQuotaExceededError):
                c.create(model="claude-sonnet-4-20250514", system="sys", messages=[{"role": "user", "content": "hi"}])

    def test_non_quota_errors_are_not_wrapped_as_quota_exceeded(self):
        from claude.client import AIQuotaExceededError, DualAIClient

        c = DualAIClient(anthropic_api_key="ant-key", kimi_api_key="kimi-key")

        with (
            patch.object(c, "_generate_kimi", side_effect=RuntimeError("Kimi down")),
            patch.object(c, "_generate_claude", side_effect=RuntimeError("Claude down")),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                c.create(model="kimi-1.0", system="sys", messages=[{"role": "user", "content": "hi"}])

        assert not isinstance(exc_info.value, AIQuotaExceededError)


# ---------------------------------------------------------------------------
# SECTION 6 — Run lifecycle store (storage/runs.py)
# ---------------------------------------------------------------------------

class TestRunsStore:
    """create → step transitions → patch → get/list, backed by an in-memory Mongo."""

    def _db_patch(self):
        from mongomock_motor import AsyncMongoMockClient
        db = AsyncMongoMockClient()["aria_test"]
        return patch("storage.runs._get_db", return_value=db)

    def _event(self, repo_name="org/app"):
        from webhook.models import GitHubPushEvent
        return GitHubPushEvent(
            event_type="push", repo_name=repo_name, branch="main", author="dev",
            commit_messages=["feat: x"], changed_files=["a.py"], diff_summary="d",
        )

    def test_create_seeds_steps_and_strips_id(self):
        from storage import runs
        with self._db_patch():
            asyncio.run(runs.create_run("run123", self._event()))
            doc = asyncio.run(runs.get_run("run123"))
        assert doc["run_id"] == "run123"
        assert doc["status"] == "running"
        assert len(doc["steps"]) == 11
        assert all(s["status"] == "pending" for s in doc["steps"])
        assert "_id" not in doc

    def test_step_transitions(self):
        from storage import runs
        with self._db_patch():
            asyncio.run(runs.create_run("r2", self._event()))
            asyncio.run(runs.start_step("r2", "analyze"))
            asyncio.run(runs.finish_step("r2", "analyze", output="kind=api"))
            doc = asyncio.run(runs.get_run("r2"))
        step = next(s for s in doc["steps"] if s["key"] == "analyze")
        assert step["status"] == "done"
        assert step["output"] == "kind=api"
        assert step["started_at"] and step["finished_at"]

    def test_patch_and_list(self):
        from storage import runs
        with self._db_patch():
            asyncio.run(runs.create_run("r3", self._event()))
            asyncio.run(runs.patch_run("r3", status="completed", bug_summary="boom"))
            doc = asyncio.run(runs.get_run("r3"))
            listed = asyncio.run(runs.list_runs())
        assert doc["status"] == "completed"
        assert doc["bug_summary"] == "boom"
        assert any(r["run_id"] == "r3" for r in listed)

    def test_get_missing_returns_none(self):
        from storage import runs
        with self._db_patch():
            assert asyncio.run(runs.get_run("nope")) is None

    def test_list_runs_filters_by_repo_and_list_repos(self):
        from storage import runs
        with self._db_patch():
            asyncio.run(runs.create_run("r4", self._event("org/app")))
            asyncio.run(runs.create_run("r5", self._event("org/other")))
            app_runs = asyncio.run(runs.list_runs(repo="org/app"))
            other_runs = asyncio.run(runs.list_runs(repo="org/other"))
            repos = asyncio.run(runs.list_repos())
        assert {r["run_id"] for r in app_runs} == {"r4"}
        assert {r["run_id"] for r in other_runs} == {"r5"}
        assert set(repos) == {"org/app", "org/other"}


# ---------------------------------------------------------------------------
# SECTION 7 — Dashboard read API (api/router.py)
# ---------------------------------------------------------------------------

class TestDashboardAPI:
    def _client(self):
        from main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_list_runs(self):
        with patch("storage.runs.list_runs", new_callable=AsyncMock, return_value=[{"run_id": "a", "repo": "o/r"}]):
            r = self._client().get("/api/runs")
        assert r.status_code == 200
        assert r.json()["runs"][0]["run_id"] == "a"

    def test_list_runs_passes_repo_filter_through(self):
        with patch("storage.runs.list_runs", new_callable=AsyncMock, return_value=[]) as mock_list:
            r = self._client().get("/api/runs?repo=org/app")
        assert r.status_code == 200
        mock_list.assert_called_once_with(limit=20, skip=0, repo="org/app")

    def test_list_repos(self):
        with patch("storage.runs.list_repos", new_callable=AsyncMock, return_value=["org/app", "org/other"]):
            r = self._client().get("/api/repos")
        assert r.status_code == 200
        assert r.json()["repos"] == ["org/app", "org/other"]

    def test_get_run_found(self):
        with patch("storage.runs.get_run", new_callable=AsyncMock, return_value={"run_id": "a", "status": "completed"}):
            r = self._client().get("/api/runs/a")
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_get_run_404(self):
        with patch("storage.runs.get_run", new_callable=AsyncMock, return_value=None):
            r = self._client().get("/api/runs/nope")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# SECTION 8 — Public URL + Discord start ping
# ---------------------------------------------------------------------------

class TestPublicUrlAndStartPing:
    def test_run_link_precedence(self):
        import runtime
        from config import settings

        old = settings.public_base_url
        try:
            settings.public_base_url = ""
            runtime.set_ngrok_url("")
            assert runtime.run_link("abc") == "http://localhost:8000/ui?run=abc"

            runtime.set_ngrok_url("https://x.ngrok.io")
            assert runtime.run_link("abc") == "https://x.ngrok.io/ui?run=abc"

            settings.public_base_url = "https://prod.example.com/"
            assert runtime.run_link("abc") == "https://prod.example.com/ui?run=abc"
        finally:
            settings.public_base_url = old
            runtime.set_ngrok_url("")

    def test_start_ping_skipped_when_discord_disabled(self):
        from integrations import discord
        with patch.object(discord.settings, "discord_enabled", False):
            msg = asyncio.run(discord.post_run_started(_make_event(), "rid", "http://x/ui?run=rid"))
        assert msg == ""

    def test_start_ping_includes_link_when_enabled(self):
        from integrations import discord

        captured = {}

        async def fake_post(embeds):
            captured["embeds"] = embeds
            return "mid"

        with (
            patch.object(discord.settings, "discord_enabled", True),
            patch.object(discord.settings, "discord_webhook_url", "http://hook"),
            patch("integrations.discord._post_embeds", side_effect=fake_post),
        ):
            msg = asyncio.run(discord.post_run_started(_make_event(), "rid", "http://x/ui?run=rid"))

        assert msg == "mid"
        assert "http://x/ui?run=rid" in json.dumps(captured["embeds"])


class TestFinalReportLink:
    def test_embed_carries_run_link(self):
        from integrations.discord import _build_embed
        from claude.analyzer import TestPlan
        from testing.result_parser import TestResult

        embed = _build_embed("r1", _make_event(), TestPlan(reasoning="r"), TestResult(), "", None, "http://x/ui?run=r1")
        assert "http://x/ui?run=r1" in json.dumps(embed["fields"])
