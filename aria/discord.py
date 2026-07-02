import requests

# Discord caps a message's content at 2000 chars; stay under it with a margin.
CONTENT_LIMIT = 1900


def _send(webhook_url, content):
    requests.post(webhook_url, json={"content": content}, timeout=15)


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

    _send(webhook_url, "\n".join(lines))


def post_evaluation(webhook_url, report, run_url, trigger=None):
    """Post a product evaluation report + manual test cases (Markdown) after a
    successful deployment. The report is chunked to respect Discord's limit."""
    if not webhook_url:
        return

    header = ["📋 **ARIA Product Evaluation** — successful deployment"]
    if trigger:
        header.append(f"triggered by: {trigger}")
    header.append(f"CI run: {run_url}")
    _send(webhook_url, "\n".join(header))

    for chunk in _chunks(report, CONTENT_LIMIT):
        _send(webhook_url, chunk)
