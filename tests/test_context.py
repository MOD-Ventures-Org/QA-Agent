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
