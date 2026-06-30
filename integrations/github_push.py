"""Pushes AI-generated test files and GitHub Actions workflow back to the target
repository branch via the GitHub Contents API.

Each file is created or updated with a single API call. The commit message always
contains ARIA_COMMIT_MARKER so ARIA's own webhook handler skips the resulting push
event and avoids an infinite loop.
"""

import base64
from typing import Dict, Optional

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

_GITHUB_API = "https://api.github.com"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode()


async def push_files_to_branch(
    repo_name: str,
    branch: str,
    files: Dict[str, str],
    token: str,
    commit_message: str,
) -> bool:
    """Create or update files on a GitHub branch via the Contents API.

    Args:
        repo_name:      GitHub ``owner/repo`` string.
        branch:         Branch to push to (e.g. ``"main"`` or a PR head branch).
        files:          Mapping of repo-relative path → file content (UTF-8 string).
        token:          GitHub personal access token or installation token with
                        ``contents: write`` permission.
        commit_message: Commit message. Must include ARIA_COMMIT_MARKER to prevent
                        re-triggering the ARIA webhook.

    Returns:
        True if every file was pushed successfully; False on the first failure.
    """
    if not token:
        logger.warning("GITHUB_TOKEN not configured — cannot push files to repo")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=30.0) as http:
        for path, content in files.items():
            url = f"{_GITHUB_API}/repos/{repo_name}/contents/{path}"

            # Fetch the current file's SHA so GitHub allows updating it.
            sha: Optional[str] = None
            try:
                r = await http.get(url, headers=headers, params={"ref": branch})
                if r.status_code == 200:
                    sha = r.json().get("sha")
            except Exception as exc:
                logger.debug("Could not fetch SHA for %s: %s", path, exc)

            body: Dict = {
                "message": commit_message,
                "content": _b64(content),
                "branch": branch,
            }
            if sha:
                body["sha"] = sha

            try:
                r = await http.put(url, headers=headers, json=body)
                if r.status_code in (200, 201):
                    action = "updated" if sha else "created"
                    logger.info("GitHub push: %s %s on %s@%s", action, path, repo_name, branch)
                else:
                    logger.error(
                        "GitHub push failed for %s: HTTP %d — %s",
                        path, r.status_code, r.text[:300],
                    )
                    return False
            except Exception as exc:
                logger.error("GitHub push error for %s: %s", path, exc)
                return False

    return True
