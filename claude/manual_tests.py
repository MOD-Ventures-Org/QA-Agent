"""Generates plain-English MANUAL test cases (for a human QA engineer) from the
pushed change, using the same Claude client as the rest of the pipeline.
"""

import json
import re
from dataclasses import dataclass, field
from typing import List

from claude.client import DualAIClient

from config import settings
from utils.logger import get_logger
from claude.prompts import MANUAL_TESTS_SYSTEM, manual_tests_user_prompt

logger = get_logger(__name__)
client = DualAIClient(
    settings.anthropic_api_key,
    settings.kimi_api_key,
    settings.kimi_model,
    settings.kimi_api_url,
)


@dataclass
class ManualTestCase:
    title: str
    steps: List[str] = field(default_factory=list)
    expected: str = ""


@dataclass
class ManualTestPlan:
    cases: List[ManualTestCase] = field(default_factory=list)


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


def _parse_cases(data: dict) -> List[ManualTestCase]:
    cases = []
    for item in data.get("cases", []):
        if not isinstance(item, dict):
            continue
        steps = [str(s) for s in item.get("steps", []) if str(s).strip()]
        cases.append(
            ManualTestCase(
                title=str(item.get("title", "Untitled case")).strip(),
                steps=steps,
                expected=str(item.get("expected", "")).strip(),
            )
        )
    return cases


async def generate_manual_tests(event, repo_context=None) -> ManualTestPlan:
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            temperature=0,
            system=MANUAL_TESTS_SYSTEM,
            messages=[{"role": "user", "content": manual_tests_user_prompt(event, repo_context)}],
        )
        raw = message.content[0].text.strip()
        data = json.loads(_strip_fences(raw))
        plan = ManualTestPlan(cases=_parse_cases(data))
        logger.info(f"Generated {len(plan.cases)} manual test case(s)")
        return plan
    except Exception as e:
        logger.error(f"Manual test generation failed: {e}")
        return ManualTestPlan()
