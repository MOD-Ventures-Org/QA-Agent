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
