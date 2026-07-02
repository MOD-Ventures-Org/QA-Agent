import json
import subprocess
from pathlib import Path

import pytest

from aria import diff


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@a.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=tmp_path, check=True)
    return tmp_path


def _commit(tmp_path, filename, content, message):
    (tmp_path / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=tmp_path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_push_event_diffs_against_before_sha(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    before_sha = _commit(tmp_path, "app.py", "print('v1')\n", "first")
    head_sha = _commit(tmp_path, "app.py", "print('v2')\n", "second")

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"before": before_sha}))

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_SHA", head_sha)

    changed = diff.get_changed_files(repo_dir=str(tmp_path))

    assert len(changed) == 1
    assert changed[0]["path"] == "app.py"
    assert changed[0]["status"] == "M"
    assert "v2" in changed[0]["patch"]


def test_push_event_handles_new_branch_zero_sha(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    head_sha = _commit(tmp_path, "app.py", "print('v1')\n", "first")

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"before": "0" * 40}))

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_SHA", head_sha)

    changed = diff.get_changed_files(repo_dir=str(tmp_path))

    assert len(changed) == 1
    assert changed[0]["path"] == "app.py"


def test_pull_request_event_diffs_against_base_sha(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    base_sha = _commit(tmp_path, "app.py", "print('base')\n", "base commit")
    head_sha = _commit(tmp_path, "app.py", "print('head')\n", "head commit")

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({
        "pull_request": {"base": {"sha": base_sha}, "head": {"sha": head_sha}}
    }))

    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_SHA", head_sha)

    changed = diff.get_changed_files(repo_dir=str(tmp_path))

    assert len(changed) == 1
    assert changed[0]["path"] == "app.py"


def test_unsupported_event_raises(tmp_path, monkeypatch):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({}))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    with pytest.raises(ValueError):
        diff.get_changed_files(repo_dir=str(tmp_path))
