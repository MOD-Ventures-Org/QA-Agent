import asyncio
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from claude.analyzer import TestPlan
from testing.result_parser import TestResult, parse_pytest_json
from utils.logger import get_logger

logger = get_logger(__name__)
SUITES_DIR = Path(__file__).parent / "suites"
GENERATED_DIR = SUITES_DIR / "generated"

SUITE_MAP = {
    "run_ui_smoke": str(SUITES_DIR / "ui" / "test_smoke.py"),
    "run_ui_regression": str(SUITES_DIR / "ui" / "test_regression.py"),
    "run_ui_critical_paths": str(SUITES_DIR / "ui" / "test_critical_paths.py"),
    "run_api_endpoints": str(SUITES_DIR / "api" / "test_endpoints.py"),
    "run_api_auth": str(SUITES_DIR / "api" / "test_auth.py"),
    "run_api_contracts": str(SUITES_DIR / "api" / "test_contracts.py"),
    "run_functional_integration": str(SUITES_DIR / "functional" / "test_integration.py"),
    "run_functional_edge_cases": str(SUITES_DIR / "functional" / "test_edge_cases.py"),
    "run_accessibility": str(SUITES_DIR / "accessibility" / "test_axe.py"),
}


async def run_tests(test_plan: TestPlan) -> TestResult:
    paths = []
    for flag, path in SUITE_MAP.items():
        if getattr(test_plan, flag, False) and os.path.exists(path):
            paths.append(path)

    if test_plan.run_generated_tests and GENERATED_DIR.exists():
        generated = [str(p) for p in GENERATED_DIR.glob("test_*.py")]
        paths.extend(generated)

    if not paths:
        logger.info("No test suites selected — skipping run")
        return TestResult()

    report_file = Path(tempfile.gettempdir()) / f"aria_report_{uuid4().hex}.json"
    report_file.unlink(missing_ok=True)

    cmd = [
        sys.executable, "-m", "pytest",
        *paths,
        "--json-report",
        f"--json-report-file={report_file}",
        "-v",
        "--timeout=300",
    ]
    logger.info(f"Running: {' '.join(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=360)
        logger.info(f"pytest exit code: {proc.returncode}\n{stdout.decode()[:2000]}")
    except asyncio.TimeoutError:
        logger.error("pytest timed out after 360s")
        return TestResult(errors=1, failure_details=[{"name": "timeout", "error": "pytest timed out", "traceback": ""}])
    except Exception as e:
        logger.error(f"pytest launch failed: {e}")
        return TestResult(errors=1, failure_details=[{"name": "launch", "error": str(e), "traceback": ""}])

    result = parse_pytest_json(report_file)
    try:
        report_file.unlink()
    except FileNotFoundError:
        pass
    return result
