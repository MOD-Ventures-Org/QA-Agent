import json
from dataclasses import dataclass, field
from typing import List

import anthropic

from config import settings
from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from claude.prompts import ANALYZER_SYSTEM, analyzer_user_prompt

logger = get_logger(__name__)
client = anthropic.Anthropic(api_key=settings.anthropic_api_key)


@dataclass
class TestPlan:
    reasoning: str = ""
    run_ui_smoke: bool = False
    run_ui_regression: bool = False
    run_ui_critical_paths: bool = False
    run_api_endpoints: bool = False
    run_api_auth: bool = False
    run_api_contracts: bool = False
    run_functional_integration: bool = False
    run_functional_edge_cases: bool = False
    run_accessibility: bool = False
    run_generated_tests: bool = False
    priority: str = "medium"
    focus_areas: List[str] = field(default_factory=list)
    affected_pages: List[str] = field(default_factory=list)


def _all_suites_plan(reasoning: str = "Fallback: running all suites") -> TestPlan:
    return TestPlan(
        reasoning=reasoning,
        run_ui_smoke=True,
        run_ui_regression=True,
        run_ui_critical_paths=True,
        run_api_endpoints=True,
        run_api_auth=True,
        run_api_contracts=True,
        run_functional_integration=True,
        run_functional_edge_cases=True,
        run_accessibility=True,
        run_generated_tests=True,
        priority="high",
    )


async def analyze_event(event: GitHubPushEvent) -> TestPlan:
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            temperature=0,
            system=ANALYZER_SYSTEM,
            messages=[{"role": "user", "content": analyzer_user_prompt(event)}],
        )
        raw = message.content[0].text.strip()
        data = json.loads(raw)
        return TestPlan(**{k: v for k, v in data.items() if k in TestPlan.__dataclass_fields__})
    except Exception as e:
        logger.error(f"Claude analyzer failed: {e} — falling back to all suites")
        return _all_suites_plan(f"Parse error: {e}")
