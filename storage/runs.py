"""The `runs` collection — one document per pipeline execution, created when the
pipeline starts and patched as each step finishes. This is the source of truth
for the web dashboard (the existing test_runs/bug_reports/manual_tests writes are
left untouched). Every function swallows DB errors and logs, so a MongoDB outage
never breaks the pipeline.
"""

from datetime import datetime, timezone
from typing import List, Optional

from storage.models import default_steps
from storage.mongo import _get_db
from utils.logger import get_logger
from webhook.models import GitHubPushEvent

logger = get_logger(__name__)

COLLECTION = "runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_run(run_id: str, event: GitHubPushEvent) -> None:
    latest_commit = (event.commit_messages[-1].splitlines()[0] if event.commit_messages else "")
    doc = {
        "run_id": run_id,
        "status": "running",
        "repo": event.repo_name,
        "branch": event.branch,
        "event_type": event.event_type,
        "author": event.author,
        "commit": latest_commit,
        "pr_title": event.pr_title or "",
        "created_at": _now(),
        "updated_at": _now(),
        "steps": default_steps(),
        "test_plan": None,
        "test_result": None,
        "generated_tests": None,
        "manual_tests": [],
        "bug_summary": "",
        "tickets": [],
        "evaluation": None,
        "discord_message_id": "",
    }
    try:
        db = _get_db()
        await db[COLLECTION].insert_one(doc)
        logger.info(f"Created run {run_id} in MongoDB")
    except Exception as e:
        logger.error(f"MongoDB create_run failed: {e}")


async def start_step(run_id: str, key: str) -> None:
    await _set_step(run_id, key, {"status": "running", "started_at": _now()})


async def finish_step(run_id: str, key: str, output: str = "", status: str = "done", error: str = "") -> None:
    await _set_step(run_id, key, {
        "status": status,
        "output": output or "",
        "error": error or "",
        "finished_at": _now(),
    })


async def _set_step(run_id: str, key: str, fields: dict) -> None:
    update = {f"steps.$.{k}": v for k, v in fields.items()}
    update["updated_at"] = _now()
    try:
        db = _get_db()
        await db[COLLECTION].update_one(
            {"run_id": run_id, "steps.key": key},
            {"$set": update},
        )
    except Exception as e:
        logger.error(f"MongoDB step update failed ({run_id}/{key}): {e}")


async def patch_run(run_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    try:
        db = _get_db()
        await db[COLLECTION].update_one({"run_id": run_id}, {"$set": fields})
    except Exception as e:
        logger.error(f"MongoDB patch_run failed ({run_id}): {e}")


def _clean(doc: Optional[dict]) -> Optional[dict]:
    if doc is not None:
        doc.pop("_id", None)
    return doc


async def get_run(run_id: str) -> Optional[dict]:
    try:
        db = _get_db()
        return _clean(await db[COLLECTION].find_one({"run_id": run_id}))
    except Exception as e:
        logger.error(f"MongoDB get_run failed ({run_id}): {e}")
        return None


async def list_runs(limit: int = 20, skip: int = 0) -> List[dict]:
    try:
        db = _get_db()
        cursor = db[COLLECTION].find(
            {},
            {"steps": 0, "generated_tests": 0, "manual_tests": 0, "test_result": 0},
            sort=[("created_at", -1)],
            skip=skip,
            limit=limit,
        )
        docs = await cursor.to_list(length=limit)
        return [_clean(d) for d in docs]
    except Exception as e:
        logger.error(f"MongoDB list_runs failed: {e}")
        return []
