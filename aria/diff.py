import json
import os
import subprocess

EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _load_event():
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        return json.load(f)


def _first_parent_or_empty_tree(head, repo_dir):
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", f"{head}^"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return EMPTY_TREE_SHA


def _base_and_head(repo_dir="."):
    event_name = os.environ["GITHUB_EVENT_NAME"]
    event = _load_event()

    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
        head = event["pull_request"]["head"]["sha"]
        return base, head

    if event_name == "push":
        base = event["before"]
        if base == "0" * 40:
            base = EMPTY_TREE_SHA
        head = os.environ["GITHUB_SHA"]
        return base, head

    if event_name == "deployment_status":
        # Runs after a deployment finishes (success or failure). The payload
        # carries no "before" ref, so diff the deployed commit against its
        # parent to get the delta that went live.
        # ponytail: upgrade to diff against the last successful deployment's
        # sha (Deployments API) if multi-commit deploys need full coverage.
        head = event["deployment"]["sha"]
        base = _first_parent_or_empty_tree(head, repo_dir)
        return base, head

    raise ValueError(f"unsupported event for diffing: {event_name}")


def get_changed_files(repo_dir="."):
    base, head = _base_and_head(repo_dir)

    status_out = subprocess.run(
        ["git", "diff", "--name-status", base, head],
        cwd=repo_dir, capture_output=True, text=True, check=True,
    ).stdout

    files = []
    for line in status_out.splitlines():
        line = line.strip()
        if not line:
            continue
        status, path = line.split("\t", 1)
        patch = subprocess.run(
            ["git", "diff", base, head, "--", path],
            cwd=repo_dir, capture_output=True, text=True, check=True,
        ).stdout
        files.append({"path": path, "status": status, "patch": patch})
    return files
