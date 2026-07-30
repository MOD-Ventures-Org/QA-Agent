import requests

# Discord caps a message's content at 2000 chars; stay under it with a margin.
CONTENT_LIMIT = 1900

# Overrides the webhook's configured name for every message we post.
USERNAME = "Dev QA Agent"


def _send(webhook_url, content):
    requests.post(webhook_url, json={"content": content, "username": USERNAME}, timeout=15)


def _chunks(text, limit):
    """Split text into <=limit pieces, preferring line boundaries; a single
    overlong line is hard-split."""
    chunks = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def post_summary(webhook_url, created, passed, failed, run_url,
                 failed_tests=None, trigger=None):
    """Post the run report: how many tests were created/run/passed/failed, and
    the details of only the failed generated tests."""
    if not webhook_url:
        return

    if failed == 0:
        header = "✅ **ARIA QA run** — all passed"
    else:
        header = f"🔴 **ARIA QA FAILED** — {failed} failed · merge held"
    lines = [header]
    if trigger:
        lines.append(f"triggered by: {trigger}")
    lines.append(
        f"tests created: {created} · run: {passed + failed} · "
        f"passed: {passed} · failed: {failed}"
    )
    lines.append(f"CI run: {run_url}")

    for entry in failed_tests or []:
        summary = entry.get("summary") or {}
        name = summary.get("test_name") or entry["test"].rsplit("::", 1)[-1]
        lines.append("")
        title = f"❌ **{name}**"
        if summary.get("source_file"):
            title += f" — {summary['source_file']}"
        lines.append(title)
        if summary.get("purpose"):
            lines.append(summary["purpose"])
        if summary.get("steps"):
            lines.append("Steps: " + "; ".join(summary["steps"]))
        if summary.get("assertions"):
            lines.append("Checks: " + "; ".join(summary["assertions"]))

    for chunk in _chunks("\n".join(lines), CONTENT_LIMIT):
        _send(webhook_url, chunk)


def post_deployment_failure(webhook_url, run_url, provider=None, environment=None):
    """Simple notification that a deployment failed. Nothing else runs for a
    failed deployment — this message is the whole response."""
    if not webhook_url:
        return

    header = "🔴 **Deployment failed**"
    if provider:
        header += f" — {provider}"
    lines = [header]
    if environment:
        lines.append(f"env: {environment}")
    lines.append(f"CI run: {run_url}")

    _send(webhook_url, "\n".join(lines))
