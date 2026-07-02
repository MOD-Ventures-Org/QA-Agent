from pathlib import Path

MANIFEST_FILENAMES = [
    "package.json", "requirements.txt", "pyproject.toml",
    "go.mod", "Gemfile", "Cargo.toml", "composer.json",
]
README_CANDIDATES = ["README.md", "README.rst", "README.txt", "readme.md"]


def _read_if_exists(path):
    p = Path(path)
    if p.is_file():
        return p.read_text(errors="ignore")
    return None


def get_repo_context(repo_dir="."):
    base = Path(repo_dir)

    readme = None
    for name in README_CANDIDATES:
        content = _read_if_exists(base / name)
        if content is not None:
            readme = content
            break

    manifests = {}
    for name in MANIFEST_FILENAMES:
        content = _read_if_exists(base / name)
        if content is not None:
            manifests[name] = content

    return {"readme": readme, "manifests": manifests}


def build_context(changed_files, repo_dir="."):
    repo_ctx = get_repo_context(repo_dir)
    for entry in changed_files:
        entry["full_content"] = _read_if_exists(Path(repo_dir) / entry["path"])
    return {"repo": repo_ctx, "files": changed_files}
