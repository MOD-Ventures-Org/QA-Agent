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
