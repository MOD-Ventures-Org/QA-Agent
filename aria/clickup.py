import hashlib

import requests

API_BASE = "https://api.clickup.com/api/v2"


def _signature(test_names):
    joined = "|".join(sorted(test_names))
    return hashlib.sha256(joined.encode()).hexdigest()[:12]


def _headers(token):
    return {"Authorization": token, "Content-Type": "application/json"}


def find_existing_ticket(list_id, token, signature):
    resp = requests.get(
        f"{API_BASE}/list/{list_id}/task",
        headers=_headers(token),
        params={"include_closed": "false"},
        timeout=30,
    )
    resp.raise_for_status()
    marker = f"[aria-sig:{signature}]"
    for task in resp.json().get("tasks", []):
        if marker in (task.get("description") or ""):
            return task["id"]
    return None


def create_ticket(list_id, token, title, description):
    resp = requests.post(
        f"{API_BASE}/list/{list_id}/task",
        headers=_headers(token),
        json={"name": title, "description": description},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def comment_ticket(token, task_id, comment):
    resp = requests.post(
        f"{API_BASE}/task/{task_id}/comment",
        headers=_headers(token),
        json={"comment_text": comment},
        timeout=30,
    )
    resp.raise_for_status()


def _format_body(failures, run_url, marker):
    lines = [f"{len(failures)} test(s) failed in this run.", f"CI run: {run_url}", "", marker, ""]
    for f in failures:
        lines.append(f"### {f['test']}")
        lines.append("```")
        lines.append(f["output"][:2000])
        lines.append("```")
    return "\n".join(lines)


def file_ticket_for_run(list_id, token, failures, run_url):
    signature = _signature([f["test"] for f in failures])
    marker = f"[aria-sig:{signature}]"
    body = _format_body(failures, run_url, marker)

    existing = find_existing_ticket(list_id, token, signature)
    if existing:
        comment_ticket(token, existing, f"New failure on {run_url}\n\n{body}")
        return existing

    title = f"ARIA: {len(failures)} generated test(s) failing"
    return create_ticket(list_id, token, title, body)
