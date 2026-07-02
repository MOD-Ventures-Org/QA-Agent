from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class TestRunDocument:
    run_id: str
    repo: str
    branch: str
    event_type: str
    timestamp: str
    priority: str
    reasoning: str
    suite_results: Dict
    total: int
    passed: int
    failed: int
    duration: float
    regression_detected: bool
    bug_summary: str = ""
    quality_score: int = 0
    grade: str = "N/A"
    recommendation: str = "unknown"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunStep:
    key: str
    label: str
    status: str = "pending"   # pending | running | done | skipped | failed
    output: str = ""
    error: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# Ordered pipeline steps the dashboard renders as one end-to-end timeline. The
# generation half (webhook/router.py) creates the run and drives clone→push; the
# run then waits on GitHub Actions ("actions"); when the report POSTs back, the
# reporting half (webhook/results.py) attaches to the SAME run and drives
# parse→persist. See runs.find_open_run for how the two halves are correlated.
RUN_STEPS = [
    # Generation half — clone → analyze → generate → push.
    ("clone", "Clone repo & read code"),
    ("analyze", "Analyze change"),
    ("generate", "Generate customized tests"),
    ("push", "Push tests & workflow to repo"),
    # Hand-off — GitHub Actions runs the committed tests in the cloud.
    ("actions", "Run tests in GitHub Actions"),
    # Reporting half — parse → evaluate → bug report → manual → notify → persist.
    ("parse", "Parse CI test report"),
    ("evaluate", "Evaluate product quality"),
    ("bug_report", "Write bug report"),
    ("manual", "Generate manual test cases"),
    ("notify", "Send Discord & ClickUp"),
    ("persist", "Persist results & dashboard"),
]


def default_steps() -> List[dict]:
    return [RunStep(key=k, label=l).to_dict() for k, l in RUN_STEPS]


@dataclass
class BugReportDocument:
    run_id: str
    repo: str
    branch: str
    failed_tests: List[dict]
    claude_summary: str
    clickup_task_ids: List[str]
    discord_message_id: str
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)
