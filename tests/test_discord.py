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


def test_post_summary_noop_without_webhook_url():
    with patch("aria.discord.requests.post") as post:
        discord.post_summary(None, 1, 0, "https://ci/run/1")

    post.assert_not_called()
