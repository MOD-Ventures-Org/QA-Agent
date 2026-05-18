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

    summary = data.get("summary", {})
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    errors = summary.get("error", 0)
    duration = data.get("duration", 0.0)

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
