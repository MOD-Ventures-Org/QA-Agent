"""Diff fingerprint cache — lets the webhook pipeline skip AI regeneration when it
has already produced tests for an identical change.

A fingerprint is a hash of (repo, branch, changed-file paths + their contents).
Identical re-pushes (reopened PRs, no-op synchronize events, the same diff landing
on multiple branches) hash to the same value, so we generate once and reuse.

Like the rest of ``storage/``, every function swallows DB errors and logs — a
MongoDB outage degrades to "always regenerate", it never breaks the pipeline.
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional

from storage.mongo import _get_db
from utils.logger import get_logger

logger = get_logger(__name__)

COLLECTION = "test_fingerprints"


def compute_fingerprint(event, repo_context) -> str:
    """Stable 16-char hash of the change. Uses the cloned changed-file contents
    when available so a real content change always produces a new fingerprint."""
    h = hashlib.sha256()
    h.update((event.repo_name or "").encode("utf-8"))
    h.update(b"\0")
    h.update((event.branch or "").encode("utf-8"))
    contents = getattr(repo_context, "changed_file_contents", None) or {}
    for path in sorted(event.changed_files):
        h.update(b"\0")
        h.update(path.encode("utf-8"))
        h.update(b"\0")
        h.update(contents.get(path, "").encode("utf-8", "ignore"))
    return h.hexdigest()[:16]


async def is_seen(repo: str, branch: str, fingerprint: str) -> Optional[dict]:
    """Return the stored record if this fingerprint was already generated, else None."""
    try:
        db = _get_db()
        return await db[COLLECTION].find_one(
            {"repo": repo, "branch": branch, "fingerprint": fingerprint}
        )
    except Exception as e:
        logger.error(f"MongoDB fingerprint lookup failed: {e}")
        return None


async def mark(repo: str, branch: str, fingerprint: str, test_file: str) -> None:
    """Record that tests were generated for this fingerprint."""
    doc = {
        "repo": repo,
        "branch": branch,
        "fingerprint": fingerprint,
        "test_file": test_file,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        db = _get_db()
        await db[COLLECTION].replace_one(
            {"repo": repo, "branch": branch, "fingerprint": fingerprint},
            doc,
            upsert=True,
        )
        logger.info(f"Marked fingerprint {fingerprint} for {repo}@{branch}")
    except Exception as e:
        logger.error(f"MongoDB fingerprint mark failed: {e}")
