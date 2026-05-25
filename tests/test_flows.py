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
import hashlib
import hmac
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


def _pr_payload(repo: str = "org/repo", branch: str = "feature/login") -> dict:
    return {
        "repository": {"full_name": repo},
        "sender": {"login": "dev"},
        "pull_request": {
            "head": {"ref": branch},
            "title": "Add login feature",
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
            "reasoning": "Auth file changed — run API + auth suites",
            "run_ui_smoke": False,
            "run_ui_regression": False,
            "run_ui_critical_paths": True,
            "run_api_endpoints": True,
            "run_api_auth": True,
            "run_api_contracts": False,
            "run_functional_integration": False,
            "run_functional_edge_cases": False,
            "run_accessibility": False,
            "run_generated_tests": False,
            "priority": "high",
            "focus_areas": ["authentication"],
            "affected_pages": ["/login"],
        })

        mock_response = MagicMock()
        mock_response.content = [SimpleNamespace(text=ai_response)]

        with patch("claude.analyzer.client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            plan = asyncio.run(analyze_event(event))

        assert plan.run_api_auth is True
        assert plan.run_api_endpoints is True
        assert plan.priority == "high"
        assert plan.run_ui_smoke is False

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
            "run_ui_smoke": False,
            "run_ui_regression": False,
            "run_ui_critical_paths": False,
            "run_api_endpoints": False,
            "run_api_auth": False,
            "run_api_contracts": False,
            "run_functional_integration": False,
            "run_functional_edge_cases": False,
            "run_accessibility": False,
            "run_generated_tests": False,
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
        assert not any([
            plan.run_ui_smoke, plan.run_api_endpoints,
            plan.run_accessibility, plan.run_generated_tests,
        ])


class TestAnalyzerSadFlow:
    """Analyzer must fall back to all-suites when AI fails."""

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

        # Must run everything rather than nothing
        assert plan.run_api_endpoints is True
        assert plan.run_ui_smoke is True
        assert plan.priority == "high"

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

        assert plan.run_api_endpoints is True
        assert plan.run_ui_smoke is True

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

        assert plan.run_api_endpoints is True


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
        run_api_auth=True,
        run_api_endpoints=True,
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


class TestPipelineHappyFlow:
    """All tests pass — no bug report, no ClickUp tickets, Discord gets a green embed."""

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
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, return_value="discord-msg-id"),
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock),
        ):
            asyncio.run(_run_pipeline(_make_event()))

        # No failures → bug report and ClickUp must NOT be called
        mock_bug.assert_not_called()
        mock_clickup.assert_not_called()

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
            patch("integrations.discord.post_discord_report", new_callable=AsyncMock, return_value="") as mock_discord,
            patch("storage.mongo.save_bug_report", new_callable=AsyncMock) as mock_save_bug,
        ):
            asyncio.run(_run_pipeline(_make_event()))

        mock_bug.assert_called_once()
        mock_cu.assert_called_once()
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
