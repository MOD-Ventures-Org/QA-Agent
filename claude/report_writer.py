import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from claude.client import AIQuotaExceededError, DualAIClient

from config import settings
from utils.logger import get_logger
from claude.analyzer import TestPlan
from claude.prompts import REPORT_WRITER_SYSTEM, report_writer_user_prompt

logger = get_logger(__name__)
client = DualAIClient(
    settings.anthropic_api_key,
    settings.kimi_api_key,
    settings.kimi_model,
    settings.kimi_api_url,
)


@dataclass
class BugReportItem:
    test_name: str
    title: str
    description: str


@dataclass
class BugReport:
    summary: str = ""
    items: List[BugReportItem] = field(default_factory=list)

    def item_for(self, test_name: str) -> Optional[BugReportItem]:
        return next((i for i in self.items if i.test_name == test_name), None)


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


def plain_title(test_name: str) -> str:
    """Best-effort plain-English title derived from a pytest node id, used when
    the AI doesn't return one (or is unreachable)."""
    name = (test_name or "").split("::")[-1]
    if name.startswith("test_"):
        name = name[len("test_"):]
    words = name.replace("_", " ").strip()
    return f"{words[:1].upper()}{words[1:]} is broken" if words else "A test is failing"


def _fallback_bug_report(failures: list) -> BugReport:
    items = [
        BugReportItem(
            test_name=f.get("name", ""),
            title=plain_title(f.get("name", "")),
            description=f"This check failed with: {(f.get('error') or 'an unexpected error')[:200]}",
        )
        for f in failures
    ]
    count = len(failures)
    names = ", ".join(f.get("name", "a test") for f in failures[:5])
    more = f" and {count - 5} more" if count > 5 else ""
    summary = (
        f"{count} automated test(s) failed: {names}{more}. "
        f"A detailed AI-written summary could not be generated this run; "
        f"please review the failing tests above."
    )
    return BugReport(summary=summary, items=items)


async def write_bug_report(test_plan: TestPlan, test_result) -> BugReport:
    failures = test_result.failure_details or []
    if not failures:
        return BugReport(summary="No failures to report.")
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1536,
            temperature=0,
            system=REPORT_WRITER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": report_writer_user_prompt(test_plan.reasoning, failures),
                }
            ],
        )
        raw = message.content[0].text.strip()
        data = json.loads(_strip_fences(raw))
        summary = str(data.get("summary", "")).strip()
        if not summary:
            raise ValueError("Report writer returned an empty summary")
        items = [
            BugReportItem(
                test_name=str(it.get("test_name", "")).strip(),
                title=str(it.get("title", "")).strip() or "A test is failing",
                description=str(it.get("description", "")).strip(),
            )
            for it in data.get("items", [])
            if isinstance(it, dict)
        ]
        return BugReport(summary=summary, items=items)
    except AIQuotaExceededError:
        raise
    except Exception as e:
        logger.error(f"Report writer failed: {e}")
        # Plain-English fallback built from the failures themselves, so the report
        # is still useful when the AI service is unreachable.
        return _fallback_bug_report(failures)
