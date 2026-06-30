"""Clones the pushed repository and extracts the context Claude needs to make
a targeted test-plan decision: README, file tree, changed-file contents, and a
backend/frontend repo-type signal.

The clone is shallow and best-effort: if it fails (network, auth, missing repo)
we degrade gracefully to a payload-only context so the pipeline still runs.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

BACKEND_NAME_PATTERNS = ("backend", "/backend", "api", "server")
FRONTEND_NAME_PATTERNS = ("frontend", "/frontend", "web", "client", "ui")
BACKEND_FILE_SIGNALS = (
    "requirements.txt", "pyproject.toml", "go.mod", "pom.xml",
    "build.gradle", "Cargo.toml", "manage.py", "Gemfile",
)
FRONTEND_FILE_SIGNALS = (
    "package.json", "next.config.js", "vite.config.js",
    "angular.json", "tsconfig.json", "svelte.config.js",
)
IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".pytest_cache"}

MAX_README_CHARS = 4000
MAX_CLAUDE_MD_CHARS = 4000
MAX_FILE_CHARS = 3000
MAX_TREE_ENTRIES = 200
MAX_TREE_DEPTH = 3
MAX_CHANGED_FILES = 8


@dataclass
class RepoContext:
    repo_type: str = "unknown"  # "backend" | "frontend" | "unknown"
    readme: str = ""
    claude_md: str = ""         # target repo's CLAUDE.md — testing conventions for the generator
    file_tree: str = ""
    changed_file_contents: Dict[str, str] = field(default_factory=dict)
    cloned: bool = False
    local_path: Optional[str] = None

    def cleanup(self) -> None:
        if self.local_path and os.path.isdir(self.local_path):
            shutil.rmtree(self.local_path, ignore_errors=True)
            self.local_path = None


def detect_repo_type(repo_name: str, file_names: List[str]) -> str:
    name = (repo_name or "").lower()
    basenames = {os.path.basename(f) for f in file_names}
    is_backend = any(p in name for p in BACKEND_NAME_PATTERNS) or any(s in basenames for s in BACKEND_FILE_SIGNALS)
    is_frontend = any(p in name for p in FRONTEND_NAME_PATTERNS) or any(s in basenames for s in FRONTEND_FILE_SIGNALS)
    if is_backend and not is_frontend:
        return "backend"
    if is_frontend and not is_backend:
        return "frontend"
    return "unknown"


def _clone(repo_name: str, branch: str, token: str, dest: str) -> bool:
    if token:
        url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
    else:
        url = f"https://github.com/{repo_name}.git"
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, dest]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort clone
        logger.warning("Repo clone failed for %s [%s]: %s", repo_name, branch, exc)
        return False


def _read_readme(root: Path) -> str:
    for name in ("README.md", "README.MD", "readme.md", "Readme.md", "README.rst", "README.txt", "README"):
        path = root / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="ignore")[:MAX_README_CHARS]
            except Exception:
                return ""
    return ""


def _read_claude_md(root: Path) -> str:
    """Read the target repo's CLAUDE.md (testing conventions, architecture rules)
    so generated tests follow the repo's documented practices."""
    for name in ("CLAUDE.md", "claude.md", "Claude.md", ".claude/CLAUDE.md"):
        path = root / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="ignore")[:MAX_CLAUDE_MD_CHARS]
            except Exception:
                return ""
    return ""


def _build_tree(root: Path) -> str:
    lines: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > MAX_TREE_DEPTH:
            dirnames[:] = []
            continue
        for filename in sorted(filenames):
            entry = filename if rel == "." else os.path.normpath(os.path.join(rel, filename))
            lines.append(entry.replace(os.sep, "/"))
            if len(lines) >= MAX_TREE_ENTRIES:
                lines.append("... (tree truncated)")
                return "\n".join(lines)
    return "\n".join(lines)


def _read_changed_files(root: Path, changed_files: List[str]) -> Dict[str, str]:
    contents: Dict[str, str] = {}
    for rel in changed_files[:MAX_CHANGED_FILES]:
        path = root / rel
        if path.is_file():
            try:
                contents[rel] = path.read_text(encoding="utf-8", errors="ignore")[:MAX_FILE_CHARS]
            except Exception:
                continue
    return contents


def build_repo_context(event, github_token: str = "") -> RepoContext:
    """Clone the event's repo/branch and extract README, tree, and changed files.

    In a CI environment (GitHub Actions), reuses the existing workspace checkout
    instead of cloning. Degrades to a payload-only RepoContext when cloning fails.
    """
    # GitHub Actions already has the repo checked out — skip the clone.
    ci_workspace = os.environ.get("GITHUB_WORKSPACE", "")
    if ci_workspace and os.path.isdir(ci_workspace):
        logger.info("CI workspace detected at %s — skipping clone", ci_workspace)
        root = Path(ci_workspace)
        try:
            top_level = [p.name for p in root.iterdir()]
        except Exception:
            top_level = []
        ctx = RepoContext(
            repo_type=detect_repo_type(event.repo_name, top_level + list(event.changed_files)),
            readme=_read_readme(root),
            claude_md=_read_claude_md(root),
            file_tree=_build_tree(root),
            changed_file_contents=_read_changed_files(root, event.changed_files),
            cloned=True,
            local_path=None,  # don't clean up CI workspace
        )
        logger.info(
            "Repo context built from CI workspace: type=%s readme=%dch tree=%d files=%d",
            ctx.repo_type, len(ctx.readme),
            ctx.file_tree.count("\n") + 1 if ctx.file_tree else 0,
            len(ctx.changed_file_contents),
        )
        return ctx

    dest = tempfile.mkdtemp(prefix="aria_repo_")
    if not _clone(event.repo_name, event.branch, github_token, dest):
        shutil.rmtree(dest, ignore_errors=True)
        return RepoContext(repo_type=detect_repo_type(event.repo_name, event.changed_files))

    root = Path(dest)
    try:
        top_level = [p.name for p in root.iterdir()]
    except Exception:
        top_level = []

    ctx = RepoContext(
        repo_type=detect_repo_type(event.repo_name, top_level + list(event.changed_files)),
        readme=_read_readme(root),
        claude_md=_read_claude_md(root),
        file_tree=_build_tree(root),
        changed_file_contents=_read_changed_files(root, event.changed_files),
        cloned=True,
        local_path=dest,
    )
    logger.info(
        "Repo context built for %s: type=%s readme=%dch tree=%d files=%d",
        event.repo_name, ctx.repo_type, len(ctx.readme),
        ctx.file_tree.count("\n") + 1 if ctx.file_tree else 0,
        len(ctx.changed_file_contents),
    )
    return ctx
