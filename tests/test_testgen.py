import json
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


def test_generate_tests_writes_human_readable_summary_json(tmp_path):
    changed_files = [{"path": "app.py", "patch": "+x", "full_content": "x = 1\n"}]
    repo_context = {"repo": {"readme": None, "manifests": {}}, "files": changed_files}
    output_dir = tmp_path / "generated"

    raw = (
        "def test_signup_creates_account():\n    assert True\n"
        + "\n" + testgen.SUMMARY_MARKER + "\n"
        + json.dumps({
            "test_name": "test_signup_creates_account",
            "purpose": "Verifies signing up with a valid email creates an account.",
            "steps": ["Submit the signup form", "Read the response"],
            "assertions": ["Response status is 201", "Response includes a user id"],
        })
    )

    with patch("aria.testgen.llm.generate", return_value=raw):
        result = testgen.generate_tests(changed_files, repo_context, output_dir)

    summary = result[0]["summary"]
    assert summary["test_name"] == "test_signup_creates_account"
    assert summary["purpose"].startswith("Verifies signing up")
    assert summary["steps"] == ["Submit the signup form", "Read the response"]
    assert summary["assertions"] == ["Response status is 201", "Response includes a user id"]

    json_path = Path(result[0]["path"]).with_suffix(".json")
    on_disk = json.loads(json_path.read_text())
    assert on_disk["source_file"] == "app.py"
    assert on_disk["kind"] == "backend"
    assert on_disk["test_name"] == "test_signup_creates_account"


def test_generate_tests_falls_back_to_default_summary_when_llm_omits_it(tmp_path):
    changed_files = [{"path": "app.py", "patch": "+x", "full_content": "x = 1\n"}]
    repo_context = {"repo": {"readme": None, "manifests": {}}, "files": changed_files}
    output_dir = tmp_path / "generated"

    with patch("aria.testgen.llm.generate", return_value="def test_x():\n    assert True\n"):
        result = testgen.generate_tests(changed_files, repo_context, output_dir)

    summary = result[0]["summary"]
    assert summary["test_name"] == "test_x"
    assert summary["purpose"] is None
    assert summary["steps"] == []
    assert summary["assertions"] == []


def test_generate_tests_propagates_rate_limit_error(tmp_path):
    import pytest

    from aria import llm

    changed_files = [{"path": "app.py", "patch": "+x", "full_content": "x = 1\n"}]
    repo_context = {"repo": {"readme": None, "manifests": {}}, "files": changed_files}

    with patch("aria.testgen.llm.generate", side_effect=llm.LLMRateLimitError("limited")):
        with pytest.raises(llm.LLMRateLimitError):
            testgen.generate_tests(changed_files, repo_context, tmp_path / "generated")
