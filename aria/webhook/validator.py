import hashlib
import hmac

from fastapi import HTTPException, Request

from config import settings

SUPPORTED_EVENTS = {
    "push",
    "pull_request",
    "pull_request_review",
    "release",
    "workflow_run",
}


async def validate_github_signature(request: Request) -> bytes:
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    event = request.headers.get("X-GitHub-Event", "")

    if not signature:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if event not in SUPPORTED_EVENTS:
        raise HTTPException(status_code=400, detail=f"Unsupported event type: {event}")

    return body
