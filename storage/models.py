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


# Ordered pipeline steps the dashboard renders as a timeline.
RUN_STEPS = [
    ("ai_check", "AI reachability"),
    ("clone", "Clone repo & read code"),
    ("analyze", "Analyze change"),
    ("manual_tests", "Generate manual test cases"),
    ("generate", "Generate tests"),
    ("run_tests", "Run tests"),
    ("regression", "Regression check"),
    ("evaluate", "Product evaluation"),
    ("persist", "Save to MongoDB"),
    ("tickets", "Bug summary & tickets"),
    ("report", "Discord report"),
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
