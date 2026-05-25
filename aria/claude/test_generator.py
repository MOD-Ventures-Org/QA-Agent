import os
import re
from datetime import datetime, timezone
from pathlib import Path

from claude.client import DualAIClient

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


async def generate_tests(event: GitHubPushEvent, test_plan: TestPlan):
    logger.info(f"Generating tests for {len(event.changed_files)} changed file(s)")
    file_contents = _read_file_contents(event.changed_files)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            temperature=0,
            system=TEST_GENERATOR_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": test_generator_user_prompt(event.changed_files, file_contents),
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
    except Exception as e:
        logger.error(f"Test generation failed: {e}")
