import json
from unittest.mock import patch

from aria import llm, run_ci_pipeline


def test_trigger_info_push(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert run_ci_pipeline._trigger_info() == "push"


def test_trigger_info_deployment_with_state_and_env(tmp_path, monkeypatch):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({
        "deployment": {"sha": "abc", "environment": "production"},
        "deployment_status": {"state": "failure"},
    }))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "deployment_status")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    assert run_ci_pipeline._trigger_info() == "deployment (failure) · env: production"


def _write_deployment_event(tmp_path, monkeypatch, state):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({
        "deployment": {"sha": "abc", "environment": "production"},
        "deployment_status": {"state": state},
    }))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "deployment_status")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))


def test_main_successful_deployment_posts_evaluation_and_skips_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    monkeypatch.setenv("DISCORD_ENABLED", "True")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    _write_deployment_event(tmp_path, monkeypatch, "success")

    changed = [{"path": "app.py", "patch": "+x", "status": "M"}]

    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=changed), \
         patch("aria.run_ci_pipeline.context.build_context", return_value={"repo": {}, "files": changed}), \
         patch("aria.run_ci_pipeline.evaluate.generate_evaluation", return_value="## report") as gen_eval, \
         patch("aria.run_ci_pipeline.discord.post_evaluation") as post_eval, \
         patch("aria.run_ci_pipeline.testgen.generate_tests") as gen_tests, \
         patch("aria.run_ci_pipeline.runner.run_tests") as run_tests:

        exit_code = run_ci_pipeline.main()

    assert exit_code == 0
    gen_eval.assert_called_once()
    post_eval.assert_called_once()
    # evaluation mode replaces the automated-test flow
    gen_tests.assert_not_called()
    run_tests.assert_not_called()


def test_main_failed_deployment_still_runs_automated_tests(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    monkeypatch.setenv("CLICKUP_ENABLED", "False")
    monkeypatch.setenv("DISCORD_ENABLED", "False")
    _write_deployment_event(tmp_path, monkeypatch, "failure")

    changed = [{"path": "app.py", "patch": "+x", "status": "M"}]
    generated = [{"path": "x.py", "source_file": "app.py", "kind": "backend"}]
    results = {"passed": 1, "failed": 0, "failures": []}

    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=changed), \
         patch("aria.run_ci_pipeline.context.build_context", return_value={"repo": {}, "files": changed}), \
         patch("aria.run_ci_pipeline.evaluate.generate_evaluation") as gen_eval, \
         patch("aria.run_ci_pipeline.testgen.generate_tests", return_value=generated) as gen_tests, \
         patch("aria.run_ci_pipeline.runner.run_tests", return_value=results):

        exit_code = run_ci_pipeline.main()

    assert exit_code == 0
    gen_tests.assert_called_once()
    gen_eval.assert_not_called()


def test_main_holds_run_on_llm_rate_limit_no_discord_no_clickup(monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    monkeypatch.setenv("CLICKUP_ENABLED", "True")
    monkeypatch.setenv("CLICKUP_LIST_ID", "list1")
    monkeypatch.setenv("CLICKUP_API_TOKEN", "token")
    monkeypatch.setenv("DISCORD_ENABLED", "True")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")

    changed = [{"path": "app.py", "patch": "+x", "status": "M"}]

    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=changed), \
         patch("aria.run_ci_pipeline.context.build_context", return_value={"repo": {}, "files": changed}), \
         patch("aria.run_ci_pipeline.testgen.generate_tests",
               side_effect=llm.LLMRateLimitError("gemini/kimi limited")), \
         patch("aria.run_ci_pipeline.runner.run_tests") as run_tests, \
         patch("aria.run_ci_pipeline.clickup.file_ticket_for_run") as file_ticket, \
         patch("aria.run_ci_pipeline.discord.post_summary") as post_summary:

        exit_code = run_ci_pipeline.main()

    # rate limiting isn't a real failure: hold the run, don't touch the merge,
    # and don't fire any notifications or tickets — just retry next trigger.
    assert exit_code == 0
    run_tests.assert_not_called()
    file_ticket.assert_not_called()
    post_summary.assert_not_called()


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
         patch("aria.run_ci_pipeline.discord.post_generated_tests") as post_generated, \
         patch("aria.run_ci_pipeline.discord.post_summary") as post_summary:

        exit_code = run_ci_pipeline.main()

    # failures hold the merge: check fails, but reporting still ran
    assert exit_code == 1
    file_ticket.assert_called_once()
    post_generated.assert_called_once_with(
        "https://discord.example/webhook", generated,
        "https://github.com/org/repo/actions/runs/42", trigger=None,
    )
    post_summary.assert_called_once()
    assert post_summary.call_args[0][4] == "https://app.clickup.com/t/999"


def test_main_attaches_test_summary_to_clickup_failures(monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    monkeypatch.setenv("CLICKUP_ENABLED", "True")
    monkeypatch.setenv("CLICKUP_LIST_ID", "list1")
    monkeypatch.setenv("CLICKUP_API_TOKEN", "token")
    monkeypatch.setenv("DISCORD_ENABLED", "False")

    changed = [{"path": "app.py", "patch": "+x", "status": "M"}]
    generated = [{
        "path": "testing/suites/generated/test_gen_0_app_py.py",
        "source_file": "app.py", "kind": "backend",
        "summary": {
            "test_name": "test_fail",
            "purpose": "Checks the signup endpoint.",
            "steps": ["Submit form"],
            "assertions": ["Status is 201"],
        },
    }]
    results = {"passed": 0, "failed": 1,
               "failures": [{"test": "x::test_fail", "output": "boom"}]}

    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=changed), \
         patch("aria.run_ci_pipeline.context.build_context", return_value={"repo": {}, "files": changed}), \
         patch("aria.run_ci_pipeline.testgen.generate_tests", return_value=generated), \
         patch("aria.run_ci_pipeline.runner.run_tests", return_value=results), \
         patch("aria.run_ci_pipeline.clickup.file_ticket_for_run", return_value="999") as file_ticket:

        run_ci_pipeline.main()

    passed_failures = file_ticket.call_args[0][2]
    assert passed_failures[0]["summary"]["purpose"] == "Checks the signup endpoint."


def test_main_returns_1_on_test_failure_to_hold_merge(monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    monkeypatch.setenv("CLICKUP_ENABLED", "False")
    monkeypatch.setenv("DISCORD_ENABLED", "False")

    changed = [{"path": "app.py", "patch": "+x", "status": "M"}]
    generated = [{"path": "x.py", "source_file": "app.py", "kind": "backend"}]
    results = {"passed": 2, "failed": 1, "failures": [{"test": "x::test_fail", "output": "boom"}]}

    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=changed), \
         patch("aria.run_ci_pipeline.context.build_context", return_value={"repo": {}, "files": changed}), \
         patch("aria.run_ci_pipeline.testgen.generate_tests", return_value=generated), \
         patch("aria.run_ci_pipeline.runner.run_tests", return_value=results):
        assert run_ci_pipeline.main() == 1


def test_main_returns_0_when_all_tests_pass(monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", ".")
    monkeypatch.setenv("CLICKUP_ENABLED", "False")
    monkeypatch.setenv("DISCORD_ENABLED", "False")

    changed = [{"path": "app.py", "patch": "+x", "status": "M"}]
    generated = [{"path": "x.py", "source_file": "app.py", "kind": "backend"}]
    results = {"passed": 3, "failed": 0, "failures": []}

    with patch("aria.run_ci_pipeline.diff.get_changed_files", return_value=changed), \
         patch("aria.run_ci_pipeline.context.build_context", return_value={"repo": {}, "files": changed}), \
         patch("aria.run_ci_pipeline.testgen.generate_tests", return_value=generated), \
         patch("aria.run_ci_pipeline.runner.run_tests", return_value=results):
        assert run_ci_pipeline.main() == 0


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

    assert exit_code == 1
    file_ticket.assert_not_called()
    post_summary.assert_not_called()
