from unittest.mock import patch

from aria import evaluate


def test_generate_evaluation_builds_prompt_and_returns_report():
    changed = [{"path": "api/users.py", "status": "M", "patch": "+def create_user(): ..."}]
    ctx = {"repo": {"readme": "My app"}, "files": changed}

    with patch("aria.evaluate.llm.generate", return_value="## Product Evaluation Report\nok") as gen:
        report = evaluate.generate_evaluation(changed, ctx)

    assert report.startswith("## Product Evaluation Report")
    prompt = gen.call_args[0][0]
    assert "api/users.py" in prompt
    assert "create_user" in prompt
    assert "My app" in prompt
    assert "Manual Test Cases" in prompt


def test_generate_evaluation_handles_missing_readme():
    changed = [{"path": "api/users.py", "status": "M", "patch": "+x"}]
    ctx = {"repo": {"readme": None}, "files": changed}

    with patch("aria.evaluate.llm.generate", return_value="report") as gen:
        evaluate.generate_evaluation(changed, ctx)

    assert "(no README found)" in gen.call_args[0][0]
