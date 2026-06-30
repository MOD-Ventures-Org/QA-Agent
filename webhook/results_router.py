"""Receives test reports POSTed back by the ARIA-generated GitHub Actions workflow
and hands them to the reporting brain (webhook/results.py).

Auth: a shared secret in the X-Aria-Token header, compared against ARIA_CALLBACK_TOKEN.
If the token isn't configured (local/dev), the check is skipped.
"""

import hmac

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from config import settings
from utils.logger import get_logger
from webhook.results import process_results

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["results"])


@router.post("/results")
async def receive_results(
    request: Request,
    background_tasks: BackgroundTasks,
    x_aria_token: str = Header(default=""),
):
    if settings.aria_callback_token:
        if not hmac.compare_digest(x_aria_token, settings.aria_callback_token):
            logger.warning("Rejected /results callback — invalid X-Aria-Token")
            raise HTTPException(status_code=401, detail="invalid token")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    repo = payload.get("repo", "unknown")
    background_tasks.add_task(process_results, payload)
    logger.info("Results callback accepted repo=%s", repo)
    return {"status": "accepted", "repo": repo}
