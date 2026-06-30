import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class TestResult:
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    duration: float = 0.0
    suite_results: Dict[str, dict] = field(default_factory=dict)
    failure_details: List[dict] = field(default_factory=list)
    regression_detected: bool = False


def parse_pytest_json(report_path: str) -> TestResult:
    path = Path(report_path)
    if not path.exists():
        return TestResult(errors=1, failure_details=[{"name": "report", "error": "Report file not found", "traceback": ""}])

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    return parse_pytest_dict(data)


def parse_pytest_dict(data: dict) -> TestResult:
    """Parse an already-loaded pytest-json-report dict into a TestResult.

    Used both by the local file reader above and by the ARIA ``/results`` callback,
    which receives the report inline in the POST body from GitHub Actions.
    """
    data = data or {}
    summary = data.get("summary", {})
    total = summary.get("total")
    passed = summary.get("passed")
    failed = summary.get("failed")
    errors = summary.get("error")
    duration = data.get("duration", 0.0)

    tests = data.get("tests", [])
    computed = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for test in tests:
        outcome = test.get("outcome")
        if outcome in computed:
            computed[outcome] += 1

    total = total if total is not None else len(tests)
    passed = passed if passed is not None else computed["passed"]
    failed = failed if failed is not None else computed["failed"]
    errors = errors if errors is not None else computed["error"]

    failure_details = []
    for test in data.get("tests", []):
        if test.get("outcome") in ("failed", "error"):
            call = test.get("call", {})
            failure_details.append({
                "name": test.get("nodeid", "unknown"),
                "error": call.get("crash", {}).get("message", ""),
                "traceback": call.get("longrepr", "")[:1000],
            })

    suite_results: dict = {}
    for test in data.get("tests", []):
        node = test.get("nodeid", "")
        suite = node.split("/")[0] if "/" in node else "root"
        if suite not in suite_results:
            suite_results[suite] = {"passed": 0, "failed": 0}
        if test.get("outcome") == "passed":
            suite_results[suite]["passed"] += 1
        elif test.get("outcome") in ("failed", "error"):
            suite_results[suite]["failed"] += 1

    return TestResult(
        total=total,
        passed=passed,
        failed=failed,
        errors=errors,
        duration=duration,
        suite_results=suite_results,
        failure_details=failure_details,
    )
