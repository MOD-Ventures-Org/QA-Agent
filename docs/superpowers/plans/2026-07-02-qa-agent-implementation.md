# ARIA QA Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `aria` Python package + reusable GitHub Actions workflow described in `docs/superpowers/specs/2026-07-02-qa-agent-design.md`: diff a repo's changes, generate tests via an LLM fallback chain, run them, and report failures to ClickUp/Discord — report-only, never blocking merges.

**Architecture:** A flat `aria/` package of single-purpose modules (`diff`, `context`, `llm`, `testgen`, `runner`, `clickup`, `discord`) wired together by `aria/run_ci_pipeline.py`, plus a `workflow_call` reusable workflow (`.github/workflows/qa-pipeline.yml`) that any repo can call with an 8-line caller workflow.

**Tech Stack:** Python 3.11, `requests` (only third-party HTTP dep — no per-provider SDKs), `pytest` + `pytest-playwright` for both the generated tests and this repo's own tests, stdlib `unittest.mock` for mocking HTTP calls in tests.

## Global Constraints

- No Mongo, no external state store — ClickUp dedup is a live API search, not stored history.
- Pipeline always exits 0 — generated-test failures are reported, never block CI.
- Generated tests are ephemeral (`testing/suites/generated/`), never committed back to the target repo.
- LLM provider order is fixed: Gemini → Claude → Kimi (first success wins).
- One ClickUp ticket per CI run (not per failing test); dedup via ClickUp task search, not local storage.
- Consumer repos integrate via `workflow_call` + `secrets: inherit`, not copy-pasted job YAML.

---

### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `aria/__init__.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: the `aria` package import root every later task's modules live under.

- [ ] **Step 1: Create `requirements.txt`**

```
requests>=2.31
pytest>=8.0
pytest-playwright>=0.5
playwright>=1.40
```

- [ ] **Step 2: Create `aria/__init__.py`**

```python
```

(empty — just marks `aria/` as a package)

- [ ] **Step 3: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
testing/suites/generated/
results.xml
*.egg-info/
```

- [ ] **Step 4: Install dependencies**

Run: `pip install -r requirements.txt && playwright install chromium --with-deps`
Expected: completes without error.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt aria/__init__.py .gitignore
git commit -m "chore: scaffold aria package"
```

---

### Task 2: `diff.py` — changed-file extraction

**Files:**
- Create: `aria/diff.py`
- Test: `tests/test_diff.py`

**Interfaces:**
- Produces: `get_changed_files(repo_dir=".") -> list[dict]`, each dict `{"path": str, "status": str, "patch": str}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diff.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_diff.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aria.diff'`

- [ ] **Step 3: Write minimal implementation**

```python
# aria/diff.py
import json
import os
import subprocess

EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _load_event():
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        return json.load(f)


def _base_and_head():
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

    raise ValueError(f"unsupported event for diffing: {event_name}")


def get_changed_files(repo_dir="."):
    base, head = _base_and_head()

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_diff.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add aria/diff.py tests/test_diff.py
git commit -m "feat: extract changed files from push/PR events"
```

---

### Task 3: `context.py` — repo context gathering

**Files:**
- Create: `aria/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: changed-file dicts from `diff.get_changed_files()` — needs the `"path"` key.
- Produces: `get_repo_context(repo_dir=".") -> dict` with keys `"readme"` (str or None) and `"manifests"` (dict of filename->content). `build_context(changed_files, repo_dir=".") -> dict` with keys `"repo"` (the above) and `"files"` (the input list, each entry mutated in place with an added `"full_content"` key, str or None if the file no longer exists on disk).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context.py
from pathlib import Path

from aria import context


def test_get_repo_context_finds_readme_and_manifest(tmp_path):
    (tmp_path / "README.md").write_text("# My App\n")
    (tmp_path / "package.json").write_text('{"name": "app"}')

    ctx = context.get_repo_context(repo_dir=str(tmp_path))

    assert ctx["readme"] == "# My App\n"
    assert ctx["manifests"]["package.json"] == '{"name": "app"}'
    assert "requirements.txt" not in ctx["manifests"]


def test_get_repo_context_handles_missing_files(tmp_path):
    ctx = context.get_repo_context(repo_dir=str(tmp_path))

    assert ctx["readme"] is None
    assert ctx["manifests"] == {}


def test_build_context_adds_full_content_for_existing_file(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')\n")
    changed_files = [{"path": "app.py", "status": "M", "patch": "..."}]

    result = context.build_context(changed_files, repo_dir=str(tmp_path))

    assert result["files"][0]["full_content"] == "print('hello')\n"
    assert result["repo"]["readme"] is None


def test_build_context_none_for_deleted_file(tmp_path):
    changed_files = [{"path": "gone.py", "status": "D", "patch": "..."}]

    result = context.build_context(changed_files, repo_dir=str(tmp_path))

    assert result["files"][0]["full_content"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aria.context'`

- [ ] **Step 3: Write minimal implementation**

```python
# aria/context.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_context.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add aria/context.py tests/test_context.py
git commit -m "feat: gather repo readme/manifest/full-file context"
```

---

### Task 4: `llm.py` — Gemini → Claude → Kimi fallback chain

**Files:**
- Create: `aria/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `generate(prompt: str) -> str`, `LLMError(Exception)`. Internal `_call_gemini`, `_call_claude`, `_call_kimi` each take `(prompt: str) -> str` and raise `LLMError` on failure (missing key, HTTP error, bad response shape).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm.py
from unittest.mock import Mock, patch

import pytest

from aria import llm


def _fake_response(json_body, status=200):
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = Mock()
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


def test_generate_uses_gemini_when_it_succeeds(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("KIMI_API_KEY", "k-key")

    gemini_body = {"candidates": [{"content": {"parts": [{"text": "gemini test code"}]}}]}
    with patch("aria.llm.requests.post", return_value=_fake_response(gemini_body)) as post:
        result = llm.generate("write a test")

    assert result == "gemini test code"
    assert post.call_count == 1
    assert "generativelanguage.googleapis.com" in post.call_args[0][0]


def test_generate_falls_back_to_claude_when_gemini_fails(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("KIMI_API_KEY", "k-key")

    claude_body = {"content": [{"text": "claude test code"}]}

    def side_effect(url, **kwargs):
        if "generativelanguage" in url:
            raise Exception("gemini down")
        return _fake_response(claude_body)

    with patch("aria.llm.requests.post", side_effect=side_effect):
        result = llm.generate("write a test")

    assert result == "claude test code"


def test_generate_falls_back_to_kimi_when_gemini_and_claude_fail(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("KIMI_API_KEY", "k-key")

    kimi_body = {"choices": [{"message": {"content": "kimi test code"}}]}

    def side_effect(url, **kwargs):
        if "generativelanguage" in url or "anthropic" in url:
            raise Exception("down")
        return _fake_response(kimi_body)

    with patch("aria.llm.requests.post", side_effect=side_effect):
        result = llm.generate("write a test")

    assert result == "kimi test code"


def test_generate_raises_when_all_providers_fail(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    with pytest.raises(llm.LLMError):
        llm.generate("write a test")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aria.llm'`

- [ ] **Step 3: Write minimal implementation**

```python
# aria/llm.py
import os

import requests


class LLMError(Exception):
    pass


def _call_gemini(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("GEMINI_API_KEY not set")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-flash-latest:generateContent?key={api_key}"
    )
    resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_claude(prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY not set")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-5",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


def _call_kimi(prompt):
    api_key = os.environ.get("KIMI_API_KEY")
    if not api_key:
        raise LLMError("KIMI_API_KEY not set")
    resp = requests.post(
        "https://api.moonshot.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "moonshot-v1-32k",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


_PROVIDERS = [_call_gemini, _call_claude, _call_kimi]


def generate(prompt):
    errors = []
    for provider in _PROVIDERS:
        try:
            return provider(prompt)
        except Exception as e:
            errors.append(f"{provider.__name__}: {e}")
    raise LLMError("all LLM providers failed: " + "; ".join(errors))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add aria/llm.py tests/test_llm.py
git commit -m "feat: add Gemini/Claude/Kimi fallback LLM chain"
```

---

### Task 5: `testgen.py` — classify changes and generate test files

**Files:**
- Create: `aria/testgen.py`
- Test: `tests/test_testgen.py`

**Interfaces:**
- Consumes: `llm.generate(prompt) -> str`, `llm.LLMError`; a `changed_files` list shaped like `context.build_context()["files"]` (each dict has `"path"`, `"patch"`, `"full_content"`); a `repo_context` dict shaped like `context.build_context()`'s return value.
- Produces: `classify(path: str) -> "frontend" | "backend"`, `generate_tests(changed_files, repo_context, output_dir) -> list[dict]` where each result dict is `{"path": str, "source_file": str, "kind": str}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_testgen.py
from pathlib import Path
from unittest.mock import patch

from aria import testgen


def test_classify_frontend_by_extension():
    assert testgen.classify("src/components/Button.tsx") == "frontend"


def test_classify_frontend_by_path_hint():
    assert testgen.classify("src/pages/Home.py") == "frontend"


def test_classify_backend_default():
    assert testgen.classify("server/handlers/user.py") == "backend"


def test_generate_tests_writes_valid_file(tmp_path):
    changed_files = [
        {"path": "app.py", "patch": "+print('new')", "full_content": "print('new')\n"}
    ]
    repo_context = {"repo": {"readme": "# App", "manifests": {}}, "files": changed_files}
    output_dir = tmp_path / "generated"

    with patch("aria.testgen.llm.generate", return_value="def test_x():\n    assert True\n"):
        result = testgen.generate_tests(changed_files, repo_context, output_dir)

    assert len(result) == 1
    assert result[0]["source_file"] == "app.py"
    assert result[0]["kind"] == "backend"
    written = Path(result[0]["path"]).read_text()
    assert "def test_x" in written


def test_generate_tests_strips_markdown_fences(tmp_path):
    changed_files = [{"path": "app.py", "patch": "+x", "full_content": "x = 1\n"}]
    repo_context = {"repo": {"readme": None, "manifests": {}}, "files": changed_files}
    output_dir = tmp_path / "generated"

    fenced = "```python\ndef test_y():\n    assert True\n```"
    with patch("aria.testgen.llm.generate", return_value=fenced):
        result = testgen.generate_tests(changed_files, repo_context, output_dir)

    written = Path(result[0]["path"]).read_text()
    assert "```" not in written
    assert "def test_y" in written


def test_generate_tests_skips_deleted_files(tmp_path):
    changed_files = [{"path": "gone.py", "patch": "-x", "full_content": None}]
    repo_context = {"repo": {"readme": None, "manifests": {}}, "files": changed_files}

    result = testgen.generate_tests(changed_files, repo_context, tmp_path / "generated")

    assert result == []


def test_generate_tests_skips_invalid_generated_code(tmp_path):
    changed_files = [{"path": "app.py", "patch": "+x", "full_content": "x = 1\n"}]
    repo_context = {"repo": {"readme": None, "manifests": {}}, "files": changed_files}

    with patch("aria.testgen.llm.generate", return_value="this is not )( valid python"):
        result = testgen.generate_tests(changed_files, repo_context, tmp_path / "generated")

    assert result == []


def test_generate_tests_skips_when_llm_fails(tmp_path):
    from aria import llm

    changed_files = [{"path": "app.py", "patch": "+x", "full_content": "x = 1\n"}]
    repo_context = {"repo": {"readme": None, "manifests": {}}, "files": changed_files}

    with patch("aria.testgen.llm.generate", side_effect=llm.LLMError("all failed")):
        result = testgen.generate_tests(changed_files, repo_context, tmp_path / "generated")

    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_testgen.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aria.testgen'`

- [ ] **Step 3: Write minimal implementation**

```python
# aria/testgen.py
import re
from pathlib import Path

from aria import llm

FRONTEND_EXTENSIONS = {".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss"}
# ponytail: path-hint heuristic, upgrade to manifest-based stack detection if misclassifications show up
FRONTEND_PATH_HINTS = ("component", "page", "view", "frontend", "client", "ui")

FRONTEND_PROMPT = """You are a QA engineer. Generate a single Python Playwright test \
(pytest style, using the `page` fixture from pytest-playwright) that exercises the \
user-facing behavior of this change against the BASE_URL_FRONTEND environment variable.
Output ONLY valid Python code. No markdown fences, no explanation.

Repo README:
{readme}

Changed file: {path}
Diff:
{patch}

Full current file content:
{content}
"""

BACKEND_PROMPT = """You are a QA engineer. Generate a single Python API test \
(pytest style, using the `requests` library) that exercises this change against the \
BASE_URL_API environment variable.
Output ONLY valid Python code. No markdown fences, no explanation.

Repo README:
{readme}

Changed file: {path}
Diff:
{patch}

Full current file content:
{content}
"""


def classify(path):
    ext = Path(path).suffix.lower()
    lower = path.lower()
    if ext in FRONTEND_EXTENSIONS or any(hint in lower for hint in FRONTEND_PATH_HINTS):
        return "frontend"
    return "backend"


def _strip_code_fence(text):
    match = re.search(r"```(?:python)?\n?(.*?)```", text, re.DOTALL)
    return match.group(1) if match else text


def _build_prompt(file_entry, readme):
    template = FRONTEND_PROMPT if classify(file_entry["path"]) == "frontend" else BACKEND_PROMPT
    return template.format(
        readme=readme or "(no README found)",
        path=file_entry["path"],
        patch=file_entry["patch"],
        content=file_entry["full_content"],
    )


def generate_tests(changed_files, repo_context, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    readme = repo_context["repo"]["readme"]

    generated = []
    for i, file_entry in enumerate(changed_files):
        if file_entry.get("full_content") is None:
            continue

        kind = classify(file_entry["path"])
        prompt = _build_prompt(file_entry, readme)

        try:
            raw = llm.generate(prompt)
        except llm.LLMError as e:
            print(f"aria: skipping {file_entry['path']}: {e}")
            continue

        code = _strip_code_fence(raw)
        try:
            compile(code, "<generated>", "exec")
        except SyntaxError as e:
            print(f"aria: skipping {file_entry['path']}: generated code invalid: {e}")
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", file_entry["path"]).strip("_")
        out_path = output_dir / f"test_gen_{i}_{safe_name}.py"
        out_path.write_text(code)
        generated.append({"path": str(out_path), "source_file": file_entry["path"], "kind": kind})

    return generated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_testgen.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add aria/testgen.py tests/test_testgen.py
git commit -m "feat: classify changes and generate test files via LLM"
```

---

### Task 6: `runner.py` — execute generated tests

**Files:**
- Create: `aria/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces: `run_tests(output_dir) -> dict` shaped `{"passed": int, "failed": int, "failures": [{"test": str, "output": str}]}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runner.py
from pathlib import Path

from aria import runner


def test_run_tests_reports_pass_and_fail(tmp_path):
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    (output_dir / "test_sample.py").write_text(
        "def test_pass():\n    assert True\n\n"
        "def test_fail():\n    assert False, 'boom'\n"
    )

    result = runner.run_tests(output_dir)

    assert result["passed"] == 1
    assert result["failed"] == 1
    assert len(result["failures"]) == 1
    assert "test_fail" in result["failures"][0]["test"]
    assert "boom" in result["failures"][0]["output"]


def test_run_tests_all_pass(tmp_path):
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    (output_dir / "test_sample.py").write_text("def test_ok():\n    assert True\n")

    result = runner.run_tests(output_dir)

    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["failures"] == []


def test_run_tests_empty_dir(tmp_path):
    output_dir = tmp_path / "generated"
    output_dir.mkdir()

    result = runner.run_tests(output_dir)

    assert result == {"passed": 0, "failed": 0, "failures": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aria.runner'`

- [ ] **Step 3: Write minimal implementation**

```python
# aria/runner.py
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def run_tests(output_dir):
    output_dir = Path(output_dir)
    junit_path = output_dir / "results.xml"

    subprocess.run(
        ["pytest", str(output_dir), f"--junitxml={junit_path}", "-v"],
        capture_output=True, text=True,
    )

    if not junit_path.exists():
        return {"passed": 0, "failed": 0, "failures": []}

    tree = ET.parse(junit_path)
    passed = 0
    failed = 0
    failures = []

    for testcase in tree.getroot().iter("testcase"):
        failure_node = testcase.find("failure")
        error_node = testcase.find("error")
        node = failure_node if failure_node is not None else error_node
        if node is not None:
            failed += 1
            failures.append({
                "test": f"{testcase.get('classname')}::{testcase.get('name')}",
                "output": node.text or "",
            })
        else:
            passed += 1

    return {"passed": passed, "failed": failed, "failures": failures}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add aria/runner.py tests/test_runner.py
git commit -m "feat: run generated tests and collect junit results"
```

---

### Task 7: `clickup.py` — dedup search + create/comment ticket

**Files:**
- Create: `aria/clickup.py`
- Test: `tests/test_clickup.py`

**Interfaces:**
- Consumes: `runner.run_tests()`'s `"failures"` list, each shaped `{"test": str, "output": str}`.
- Produces: `file_ticket_for_run(list_id, token, failures, run_url) -> str` (task id, existing or new). Internal `find_existing_ticket(list_id, token, signature) -> str | None`, `create_ticket(list_id, token, title, description) -> str`, `comment_ticket(token, task_id, comment) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clickup.py
from unittest.mock import Mock, patch

from aria import clickup


def _resp(json_body):
    r = Mock()
    r.raise_for_status = Mock()
    r.json.return_value = json_body
    return r


def test_find_existing_ticket_matches_signature():
    tasks_body = {"tasks": [{"id": "123", "description": "some text [aria-sig:abcd1234ef] more"}]}
    with patch("aria.clickup.requests.get", return_value=_resp(tasks_body)):
        found = clickup.find_existing_ticket("list1", "token", "abcd1234ef")
    assert found == "123"


def test_find_existing_ticket_returns_none_when_no_match():
    tasks_body = {"tasks": [{"id": "123", "description": "unrelated"}]}
    with patch("aria.clickup.requests.get", return_value=_resp(tasks_body)):
        found = clickup.find_existing_ticket("list1", "token", "abcd1234ef")
    assert found is None


def test_file_ticket_for_run_creates_new_when_none_exists():
    failures = [{"test": "tests/test_x.py::test_fail", "output": "AssertionError"}]
    with patch("aria.clickup.find_existing_ticket", return_value=None), \
         patch("aria.clickup.create_ticket", return_value="999") as create:
        task_id = clickup.file_ticket_for_run("list1", "token", failures, "https://ci/run/1")

    assert task_id == "999"
    create.assert_called_once()
    title, body = create.call_args[0][2], create.call_args[0][3]
    assert "1 generated test(s) failing" in title
    assert "test_fail" in body


def test_file_ticket_for_run_comments_on_existing():
    failures = [{"test": "tests/test_x.py::test_fail", "output": "AssertionError"}]
    with patch("aria.clickup.find_existing_ticket", return_value="555"), \
         patch("aria.clickup.comment_ticket") as comment:
        task_id = clickup.file_ticket_for_run("list1", "token", failures, "https://ci/run/1")

    assert task_id == "555"
    comment.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clickup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aria.clickup'`

- [ ] **Step 3: Write minimal implementation**

```python
# aria/clickup.py
import hashlib

import requests

API_BASE = "https://api.clickup.com/api/v2"


def _signature(test_names):
    joined = "|".join(sorted(test_names))
    return hashlib.sha256(joined.encode()).hexdigest()[:12]


def _headers(token):
    return {"Authorization": token, "Content-Type": "application/json"}


def find_existing_ticket(list_id, token, signature):
    resp = requests.get(
        f"{API_BASE}/list/{list_id}/task",
        headers=_headers(token),
        params={"include_closed": "false"},
        timeout=30,
    )
    resp.raise_for_status()
    marker = f"[aria-sig:{signature}]"
    for task in resp.json().get("tasks", []):
        if marker in (task.get("description") or ""):
            return task["id"]
    return None


def create_ticket(list_id, token, title, description):
    resp = requests.post(
        f"{API_BASE}/list/{list_id}/task",
        headers=_headers(token),
        json={"name": title, "description": description},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def comment_ticket(token, task_id, comment):
    resp = requests.post(
        f"{API_BASE}/task/{task_id}/comment",
        headers=_headers(token),
        json={"comment_text": comment},
        timeout=30,
    )
    resp.raise_for_status()


def _format_body(failures, run_url, marker):
    lines = [f"{len(failures)} test(s) failed in this run.", f"CI run: {run_url}", "", marker, ""]
    for f in failures:
        lines.append(f"### {f['test']}")
        lines.append("```")
        lines.append(f["output"][:2000])
        lines.append("```")
    return "\n".join(lines)


def file_ticket_for_run(list_id, token, failures, run_url):
    signature = _signature([f["test"] for f in failures])
    marker = f"[aria-sig:{signature}]"
    body = _format_body(failures, run_url, marker)

    existing = find_existing_ticket(list_id, token, signature)
    if existing:
        comment_ticket(token, existing, f"New failure on {run_url}\n\n{body}")
        return existing

    title = f"ARIA: {len(failures)} generated test(s) failing"
    return create_ticket(list_id, token, title, body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_clickup.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add aria/clickup.py tests/test_clickup.py
git commit -m "feat: dedup and file ClickUp tickets for failing runs"
```

---

### Task 8: `discord.py` — run summary notification

**Files:**
- Create: `aria/discord.py`
- Test: `tests/test_discord.py`

**Interfaces:**
- Produces: `post_summary(webhook_url, passed, failed, run_url, ticket_url=None) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_discord.py
from unittest.mock import Mock, patch

from aria import discord


def test_post_summary_sends_webhook_with_counts():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary("https://discord.example/webhook", 3, 1, "https://ci/run/1")

    post.assert_called_once()
    url, kwargs = post.call_args[0][0], post.call_args[1]
    assert url == "https://discord.example/webhook"
    assert "passed: 3, failed: 1" in kwargs["json"]["content"]


def test_post_summary_includes_ticket_link_when_present():
    with patch("aria.discord.requests.post", return_value=Mock()) as post:
        discord.post_summary(
            "https://discord.example/webhook", 2, 1, "https://ci/run/1",
            ticket_url="https://app.clickup.com/t/999",
        )

    content = post.call_args[1]["json"]["content"]
    assert "https://app.clickup.com/t/999" in content


def test_post_summary_noop_without_webhook_url():
    with patch("aria.discord.requests.post") as post:
        discord.post_summary(None, 1, 0, "https://ci/run/1")

    post.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_discord.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aria.discord'`

- [ ] **Step 3: Write minimal implementation**

```python
# aria/discord.py
import requests


def post_summary(webhook_url, passed, failed, run_url, ticket_url=None):
    if not webhook_url:
        return

    status = "all passed" if failed == 0 else f"{failed} failed"
    lines = [
        f"**ARIA QA run** — {status}",
        f"passed: {passed}, failed: {failed}",
        f"CI run: {run_url}",
    ]
    if ticket_url:
        lines.append(f"ClickUp: {ticket_url}")

    requests.post(webhook_url, json={"content": "\n".join(lines)}, timeout=15)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_discord.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add aria/discord.py tests/test_discord.py
git commit -m "feat: post run summary to Discord webhook"
```

---

### Task 9: `run_ci_pipeline.py` — orchestrator

**Files:**
- Create: `aria/run_ci_pipeline.py`
- Test: `tests/test_run_ci_pipeline.py`

**Interfaces:**
- Consumes every module from Tasks 2–8 by their exact names above.
- Produces: `main() -> int` (always returns 0), invoked via `python -m aria.run_ci_pipeline`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_ci_pipeline.py
from unittest.mock import patch

from aria import run_ci_pipeline


def test_main_returns_0_and_skips_when_no_changes(monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=[]):
        assert run_ci_pipeline.main() == 0


def test_main_runs_full_pipeline_and_files_ticket_on_failure(monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    monkeypatch.setenv("CLICKUP_ENABLED", "True")
    monkeypatch.setenv("CLICKUP_LIST_ID", "list1")
    monkeypatch.setenv("CLICKUP_API_TOKEN", "token")
    monkeypatch.setenv("DISCORD_ENABLED", "True")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")

    changed = [{"path": "app.py", "patch": "+x", "status": "M"}]
    generated = [{"path": "testing/suites/generated/test_gen_0_app_py.py",
                  "source_file": "app.py", "kind": "backend"}]
    results = {"passed": 0, "failed": 1, "failures": [{"test": "x::test_fail", "output": "boom"}]}

    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=changed), \
         patch("aria.run_ci_pipeline.context.build_context", return_value={"repo": {}, "files": changed}), \
         patch("aria.run_ci_pipeline.testgen.generate_tests", return_value=generated), \
         patch("aria.run_ci_pipeline.runner.run_tests", return_value=results), \
         patch("aria.run_ci_pipeline.clickup.file_ticket_for_run", return_value="999") as file_ticket, \
         patch("aria.run_ci_pipeline.discord.post_summary") as post_summary:

        exit_code = run_ci_pipeline.main()

    assert exit_code == 0
    file_ticket.assert_called_once()
    post_summary.assert_called_once()
    assert post_summary.call_args[0][4] == "https://app.clickup.com/t/999"


def test_main_skips_clickup_when_disabled(monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    monkeypatch.setenv("CLICKUP_ENABLED", "False")
    monkeypatch.setenv("DISCORD_ENABLED", "False")

    changed = [{"path": "app.py", "patch": "+x", "status": "M"}]
    generated = [{"path": "x.py", "source_file": "app.py", "kind": "backend"}]
    results = {"passed": 0, "failed": 1, "failures": [{"test": "x::test_fail", "output": "boom"}]}

    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=changed), \
         patch("aria.run_ci_pipeline.context.build_context", return_value={"repo": {}, "files": changed}), \
         patch("aria.run_ci_pipeline.testgen.generate_tests", return_value=generated), \
         patch("aria.run_ci_pipeline.runner.run_tests", return_value=results), \
         patch("aria.run_ci_pipeline.clickup.file_ticket_for_run") as file_ticket, \
         patch("aria.run_ci_pipeline.discord.post_summary") as post_summary:

        exit_code = run_ci_pipeline.main()

    assert exit_code == 0
    file_ticket.assert_not_called()
    post_summary.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_ci_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aria.run_ci_pipeline'`

- [ ] **Step 3: Write minimal implementation**

```python
# aria/run_ci_pipeline.py
import os
import sys

from aria import clickup, context, diff, discord, runner, testgen

OUTPUT_DIR = "testing/suites/generated"


def _run_url():
    return "{}/{}/actions/runs/{}".format(
        os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    )


def main():
    repo_dir = os.environ.get("GITHUB_WORKSPACE", ".")

    changed = diff.get_changed_files(repo_dir=repo_dir)
    if not changed:
        print("aria: no changed files, nothing to do")
        return 0

    ctx = context.build_context(changed, repo_dir=repo_dir)
    generated = testgen.generate_tests(changed, ctx, OUTPUT_DIR)
    if not generated:
        print("aria: no tests generated")
        return 0

    results = runner.run_tests(OUTPUT_DIR)
    run_url = _run_url()

    ticket_url = None
    if results["failed"] > 0 and os.environ.get("CLICKUP_ENABLED", "False") == "True":
        list_id = os.environ.get("CLICKUP_LIST_ID")
        token = os.environ.get("CLICKUP_API_TOKEN")
        if list_id and token:
            task_id = clickup.file_ticket_for_run(list_id, token, results["failures"], run_url)
            ticket_url = f"https://app.clickup.com/t/{task_id}"

    if os.environ.get("DISCORD_ENABLED", "False") == "True":
        discord.post_summary(
            os.environ.get("DISCORD_WEBHOOK_URL"),
            results["passed"], results["failed"], run_url, ticket_url,
        )

    print(f"aria: passed={results['passed']} failed={results['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_run_ci_pipeline.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests across every module pass

- [ ] **Step 6: Commit**

```bash
git add aria/run_ci_pipeline.py tests/test_run_ci_pipeline.py
git commit -m "feat: wire pipeline stages together in run_ci_pipeline orchestrator"
```

---

### Task 10: Reusable workflow + caller workflow file

**Files:**
- Create: `.github/workflows/qa-pipeline.yml`
- Create: `examples/caller-workflow.yml`
- Create: `README.md`

**Interfaces:**
- Produces: the `workflow_call` entry point any consumer repo targets, and the exact file a consumer copies into their own `.github/workflows/`.

- [ ] **Step 1: Create the reusable workflow**

```yaml
# .github/workflows/qa-pipeline.yml
name: ARIA QA Pipeline (reusable)

on:
  workflow_call:
    secrets:
      ANTHROPIC_API_KEY:
        required: true
      GEMINI_API_KEY:
        required: true
      KIMI_API_KEY:
        required: true
      DISCORD_WEBHOOK_URL:
        required: false
      CLICKUP_API_TOKEN:
        required: false
      CLICKUP_LIST_ID:
        required: false
      BASE_URL_FRONTEND:
        required: false
      BASE_URL_API:
        required: false

jobs:
  aria-qa:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout target repo
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install ARIA
        run: pip install "git+https://github.com/RamaishaRehman/QA-Agent@main"

      - name: Install Playwright browsers
        run: playwright install chromium --with-deps

      - name: Run ARIA pipeline
        run: python -m aria.run_ci_pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
          DISCORD_ENABLED: ${{ secrets.DISCORD_WEBHOOK_URL != '' && 'True' || 'False' }}
          CLICKUP_API_TOKEN: ${{ secrets.CLICKUP_API_TOKEN }}
          CLICKUP_LIST_ID: ${{ secrets.CLICKUP_LIST_ID }}
          CLICKUP_ENABLED: ${{ secrets.CLICKUP_API_TOKEN != '' && 'True' || 'False' }}
          BASE_URL_FRONTEND: ${{ secrets.BASE_URL_FRONTEND }}
          BASE_URL_API: ${{ secrets.BASE_URL_API }}

      - name: Upload generated tests
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: generated-tests
          path: testing/suites/generated/
          if-no-files-found: ignore
```

- [ ] **Step 2: Create the caller workflow file (the file to drop into any repo)**

```yaml
# examples/caller-workflow.yml
# Copy this file into any repo as .github/workflows/aria-qa.yml
name: ARIA QA

on:
  pull_request:
    branches: [main, master]
    types: [opened, synchronize, reopened]
  push:
    branches: [main, master]

jobs:
  aria-qa:
    uses: RamaishaRehman/QA-Agent/.github/workflows/qa-pipeline.yml@main
    secrets: inherit
```

- [ ] **Step 3: Create a short README explaining setup**

```markdown
# ARIA QA Agent

Drops into any GitHub repo and, on every push/PR: diffs the change, generates
tests via an LLM, runs them, and reports failures — report-only, never blocks
a merge.

## Setup

1. Copy `examples/caller-workflow.yml` into your repo as
   `.github/workflows/aria-qa.yml`.
2. In your repo's secrets, set:
   - `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `KIMI_API_KEY` (required — LLM
     fallback chain tries Gemini, then Claude, then Kimi)
   - `BASE_URL_FRONTEND`, `BASE_URL_API` (optional — where generated tests
     run against; skip either to skip that test category)
   - `CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID` (optional — omit to skip ticket
     filing entirely)
   - `DISCORD_WEBHOOK_URL` (optional — omit to skip notifications)
3. Push or open a PR. Generated tests land as a `generated-tests` artifact
   on the run regardless of pass/fail.
```

- [ ] **Step 4: Verify the full test suite still passes**

Run: `pytest -v`
Expected: all tests pass (this task adds no Python, just workflow/docs files)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/qa-pipeline.yml examples/caller-workflow.yml README.md
git commit -m "feat: expose ARIA as a reusable workflow_call pipeline"
```

---

## Self-Review Notes

- **Spec coverage:** diff extraction (Task 2), context gathering (Task 3), LLM fallback chain (Task 4), test generation/classification (Task 5), test execution (Task 6), ClickUp dedup+ticket (Task 7), Discord summary (Task 8), orchestration + report-only exit (Task 9), reusable-workflow distribution (Task 10) — every section of the spec has a corresponding task.
- **Placeholder scan:** no TBD/TODO; every step has runnable code.
- **Type consistency:** `changed_files` dicts carry `path`/`status`/`patch` from Task 2 through Task 3 (`+full_content`) into Task 5 unchanged; `runner.run_tests()`'s `failures` shape (`test`/`output`) matches what Task 7's `clickup.file_ticket_for_run` and `_format_body` consume; `run_ci_pipeline.main()` calls every module by the exact names defined in its own task.
