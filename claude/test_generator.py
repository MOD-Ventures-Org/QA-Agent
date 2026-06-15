import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from claude.client import AIQuotaExceededError, DualAIClient

from config import settings
from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from claude.analyzer import TestPlan
from claude.prompts import TEST_GENERATOR_SYSTEM, test_generator_user_prompt

logger = get_logger(__name__)
client = DualAIClient(
    settings.anthropic_api_key,
    settings.kimi_api_key,
    settings.kimi_model,
    settings.kimi_api_url,
)

GENERATED_DIR = Path(__file__).parent.parent / "testing" / "suites" / "generated"
PRODUCT_CONTEXT_PATH = Path(__file__).parent.parent / "PRODUCT_CONTEXT.md"


@dataclass
class GeneratedTestSummary:
    file_name: str
    test_names: List[str] = field(default_factory=list)
    triggered_by: List[str] = field(default_factory=list)
    code: str = ""


def _sanitize_name(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return name.lower().strip("_")[:50]


def _read_file_contents(changed_files: list) -> dict:
    contents = {}
    for path in changed_files[:5]:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    contents[path] = f.read()[:3000]
        except Exception:
            pass
    return contents


def _load_product_context() -> str:
    if PRODUCT_CONTEXT_PATH.exists():
        try:
            return PRODUCT_CONTEXT_PATH.read_text(encoding="utf-8")[:4000]
        except Exception:
            pass
    return ""


def _extract_test_names(code: str) -> List[str]:
    return re.findall(r"^def (test_\w+)", code, re.MULTILINE)


async def generate_tests(event: GitHubPushEvent, test_plan: TestPlan, repo_context=None) -> Optional[GeneratedTestSummary]:
    logger.info(f"Generating tests for {len(event.changed_files)} changed file(s)")
    # Prefer file contents from the cloned repo context; fall back to local disk.
    file_contents = dict(getattr(repo_context, "changed_file_contents", None) or {})
    if not file_contents:
        file_contents = _read_file_contents(event.changed_files)
    product_context = _load_product_context()

    if product_context:
        logger.info("Product context loaded from PRODUCT_CONTEXT.md")

    repo_type = getattr(repo_context, "repo_type", "unknown")
    logger.info(f"Generating {test_plan.test_kind} tests for a {repo_type} repo")

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            temperature=0,
            system=TEST_GENERATOR_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": test_generator_user_prompt(
                        event.changed_files,
                        file_contents,
                        product_context,
                        repo_type=repo_type,
                        test_kind=test_plan.test_kind,
                        focus_areas=test_plan.focus_areas,
                        affected_pages=test_plan.affected_pages,
                    ),
                }
            ],
        )
        code = message.content[0].text.strip()
        code = re.sub(r"^```python\n?", "", code)
        code = re.sub(r"\n?```$", "", code)

        feature_name = _sanitize_name(
            event.changed_files[0].split("/")[-1].split(".")[0] if event.changed_files else "feature"
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = GENERATED_DIR / f"test_{feature_name}_{timestamp}.py"

        GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        filename.write_text(code, encoding="utf-8")
        logger.info(f"Generated test file: {filename}")

        test_names = _extract_test_names(code)
        return GeneratedTestSummary(
            file_name=filename.name,
            test_names=test_names,
            triggered_by=event.changed_files[:5],
            code=code,
        )
    except AIQuotaExceededError:
        raise
    except Exception as e:
        logger.error(f"Test generation failed: {e}")
        return None
