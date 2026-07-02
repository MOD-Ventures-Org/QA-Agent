import requests


def post_summary(webhook_url, passed, failed, run_url, ticket_url=None):
    if not webhook_url:
        return

    status = "all passed" if failed == 0 else f"{failed} failed"
    lines = [
        f"**ARIA QA run** — {status}",
        f"passed: {passed}, failed: {failed}",
        f"CI run: {run_url}",
    ]
    if ticket_url:
        lines.append(f"ClickUp: {ticket_url}")

    requests.post(webhook_url, json={"content": "\n".join(lines)}, timeout=15)
