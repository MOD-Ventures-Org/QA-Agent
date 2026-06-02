import asyncio
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


def _build_pytest_cmd(paths: list, report_file: Path, keyword: str = "") -> list:
    cmd = [
        sys.executable, "-m", "pytest",
        *paths,
        "--json-report",
        f"--json-report-file={report_file}",
        "-v",
        "--timeout=300",
    ]
    if keyword:
        cmd += ["-k", keyword]
    return cmd


async def run_tests(test_plan: TestPlan) -> TestResult:
    paths = []
    if GENERATED_DIR.exists():
        paths = [str(p) for p in GENERATED_DIR.glob("test_*.py")]

    logger.info("Running generated tests: %s", paths)

    if not paths:
        logger.info("No generated tests found — skipping run")
        return TestResult()

    report_file = Path(tempfile.gettempdir()) / f"aria_report_{uuid4().hex}.json"
    report_file.unlink(missing_ok=True)

    keyword = getattr(test_plan, "pytest_keyword", "") or ""
    if keyword:
        logger.info("Narrowing run with pytest keyword filter: -k %r", keyword)
    cmd = _build_pytest_cmd(paths, report_file, keyword)
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
