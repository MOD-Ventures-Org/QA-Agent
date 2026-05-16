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

    def to_dict(self) -> dict:
        return asdict(self)


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
