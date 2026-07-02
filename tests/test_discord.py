from unittest.mock import Mock, patch

from aria import discord


def test_post_summary_sends_webhook_with_counts():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary("https://discord.example/webhook", 3, 1, "https://ci/run/1")

    post.assert_called_once()
    url, kwargs = post.call_args[0][0], post.call_args[1]
    assert url == "https://discord.example/webhook"
    assert "passed: 3, failed: 1" in kwargs["json"]["content"]


def test_post_summary_includes_ticket_link_when_present():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary(
            "https://discord.example/webhook", 2, 1, "https://ci/run/1",
            ticket_url="https://app.clickup.com/t/999",
        )

    content = post.call_args[1]["json"]["content"]
    assert "https://app.clickup.com/t/999" in content


def test_post_summary_shows_trigger_when_present():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary(
            "https://discord.example/webhook", 5, 2, "https://ci/run/1",
            trigger="deployment (failure) · env: production",
        )

    content = post.call_args[1]["json"]["content"]
    assert "triggered by: deployment (failure) · env: production" in content


def test_post_summary_noop_without_webhook_url():
    with patch("aria.discord.requests.post") as post:
        discord.post_summary(None, 1, 0, "https://ci/run/1")

    post.assert_not_called()


def test_post_evaluation_sends_header_then_report():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_evaluation(
            "https://discord.example/webhook",
            "## Product Evaluation Report\nlooks good\n## Manual Test Cases\n1. do x",
            "https://ci/run/1",
            trigger="deployment (success) · env: production",
        )

    contents = [call.kwargs["json"]["content"] for call in post.call_args_list]
    assert "ARIA Product Evaluation" in contents[0]
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
