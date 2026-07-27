from unittest.mock import Mock, patch

from aria import discord


def test_post_summary_shows_created_run_passed_failed_counts():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary("https://discord.example/webhook", 5, 3, 1, "https://ci/run/1")

    post.assert_called_once()
    url, kwargs = post.call_args[0][0], post.call_args[1]
    assert url == "https://discord.example/webhook"
    assert "tests created: 5 · run: 4 · passed: 3 · failed: 1" in kwargs["json"]["content"]


def test_post_summary_sends_display_name_override():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary("https://discord.example/webhook", 4, 3, 1, "https://ci/run/1")

    assert post.call_args[1]["json"]["username"] == "Dev QA Agent"


def test_post_summary_lists_only_failed_tests_with_ticket_link():
    failed_tests = [{
        "test": "x::test_signup_creates_account",
        "output": "boom",
        "summary": {
            "test_name": "test_signup_creates_account",
            "source_file": "app.py",
            "purpose": "Verifies signing up with a valid email creates an account.",
            "steps": ["Submit the signup form"],
            "assertions": ["Response status is 201"],
        },
    }]

    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary(
            "https://discord.example/webhook", 5, 4, 1, "https://ci/run/1",
            failed_tests=failed_tests, ticket_url="https://app.clickup.com/t/999",
        )

    content = "\n".join(call.kwargs["json"]["content"] for call in post.call_args_list)
    assert "https://app.clickup.com/t/999" in content
    assert "test_signup_creates_account" in content
    assert "Verifies signing up with a valid email creates an account." in content
    assert "Steps: Submit the signup form" in content
    assert "Checks: Response status is 201" in content


def test_post_summary_omits_failed_section_when_all_pass():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary(
            "https://discord.example/webhook", 3, 3, 0, "https://ci/run/1",
            failed_tests=[],
        )

    content = post.call_args[1]["json"]["content"]
    assert "all passed" in content
    assert "❌" not in content


def test_post_summary_shows_trigger_when_present():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary(
            "https://discord.example/webhook", 7, 5, 2, "https://ci/run/1",
            trigger="deployment (failure) · env: production",
        )

    content = post.call_args[1]["json"]["content"]
    assert "triggered by: deployment (failure) · env: production" in content


def test_post_summary_noop_without_webhook_url():
    with patch("aria.discord.requests.post") as post:
        discord.post_summary(None, 1, 1, 0, "https://ci/run/1")

    post.assert_not_called()


def test_post_deployment_failure_sends_simple_notification():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_deployment_failure(
            "https://discord.example/webhook", "https://ci/run/1",
            provider="Vercel", environment="production",
        )

    post.assert_called_once()
    content = post.call_args[1]["json"]["content"]
    assert "Deployment failed" in content
    assert "Vercel" in content
    assert "env: production" in content
    assert "https://ci/run/1" in content


def test_post_deployment_failure_noop_without_webhook_url():
    with patch("aria.discord.requests.post") as post:
        discord.post_deployment_failure(None, "https://ci/run/1")

    post.assert_not_called()


def test_post_evaluation_sends_header_then_report():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_evaluation(
            "https://discord.example/webhook",
            "## Manual Test Cases\n1. do x",
            "https://ci/run/1",
            trigger="deployment (success) · env: production",
        )

    contents = [call.kwargs["json"]["content"] for call in post.call_args_list]
    assert "ARIA Manual Test Cases" in contents[0]
    assert "deployment (success)" in contents[0]
    assert any("Manual Test Cases" in c for c in contents[1:])


def test_post_evaluation_chunks_long_reports_under_limit():
    long_report = "\n".join(f"line {i} " + "x" * 100 for i in range(200))
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_evaluation("https://discord.example/webhook", long_report, "https://ci/run/1")

    # header + at least two report chunks, each within the Discord limit
    assert post.call_count >= 3
    for call in post.call_args_list:
        assert len(call.kwargs["json"]["content"]) <= discord.CONTENT_LIMIT


def test_post_evaluation_noop_without_webhook_url():
    with patch("aria.discord.requests.post") as post:
        discord.post_evaluation(None, "report", "https://ci/run/1")

    post.assert_not_called()


