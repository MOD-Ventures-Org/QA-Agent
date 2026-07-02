import requests


def post_summary(webhook_url, passed, failed, run_url, ticket_url=None, trigger=None):
    if not webhook_url:
        return

    if failed == 0:
        header = "✅ **ARIA QA run** — all passed"
    else:
        header = f"🔴 **ARIA QA FAILED** — {failed} failed · merge held"
    lines = [header]
    if trigger:
        lines.append(f"triggered by: {trigger}")
    lines.append(f"passed: {passed}, failed: {failed}")
    lines.append(f"CI run: {run_url}")
    if ticket_url:
        lines.append(f"ClickUp: {ticket_url}")

    requests.post(webhook_url, json={"content": "\n".join(lines)}, timeout=15)
