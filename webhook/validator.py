import hashlib
import hmac

from fastapi import HTTPException, Request

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

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

    logger.info(f"Webhook received — event={event} signature_present={bool(signature)} body_size={len(body)}")

    if not signature:
        logger.error("Rejected: missing X-Hub-Signature-256 header")
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        logger.error(f"Rejected: signature mismatch — check WEBHOOK_SECRET in .env matches GitHub webhook settings")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if event not in SUPPORTED_EVENTS:
        logger.warning(f"Rejected: unsupported event type={event}")
        raise HTTPException(status_code=400, detail=f"Unsupported event type: {event}")

    logger.info(f"Webhook validated OK — event={event}")
    return body
