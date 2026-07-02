import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def run_tests(output_dir):
    output_dir = Path(output_dir)
    junit_path = output_dir / "results.xml"

    subprocess.run(
        [sys.executable, "-m", "pytest", str(output_dir), f"--junitxml={junit_path}", "-v"],
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
