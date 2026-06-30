"""Generates a customized GitHub Actions workflow YAML for the pushed repository.

Claude inspects the repo type, test kind, and detected dependency files to produce
a workflow that sets up the right runtime, installs only the deps that exist, and
runs the AI-generated test file. The generated workflow is pushed back to the branch
by integrations/github_push.py so GitHub Actions executes it automatically.
"""

import re
from dataclasses import dataclass
from typing import Optional

from claude.client import AIQuotaExceededError, DualAIClient
from claude.prompts import WORKFLOW_GENERATOR_SYSTEM, workflow_generator_user_prompt
from config import settings
from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from claude.analyzer import TestPlan

logger = get_logger(__name__)

client = DualAIClient(
    settings.anthropic_api_key,
    settings.kimi_api_key,
    settings.kimi_model,
    settings.kimi_api_url,
)

WORKFLOW_FILE_PATH = ".github/workflows/aria_generated_tests.yml"
ARIA_WORKFLOW_NAME = "ARIA Generated Tests"
ARIA_COMMIT_MARKER = "[skip aria]"


@dataclass
class GeneratedWorkflow:
    filename: str = WORKFLOW_FILE_PATH
    content: str = ""
    test_file: str = ""


async def generate_workflow(
    event: GitHubPushEvent,
    test_plan: TestPlan,
    generated_tests,
    repo_context=None,
) -> Optional[GeneratedWorkflow]:
    test_file = getattr(generated_tests, "file_name", "") if generated_tests else ""
    if not test_file:
        logger.warning("No generated test file name — skipping workflow generation")
        return None

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            temperature=0,
            system=WORKFLOW_GENERATOR_SYSTEM,
            messages=[{
                "role": "user",
                "content": workflow_generator_user_prompt(event, test_plan, test_file, repo_context),
            }],
        )
        content = message.content[0].text.strip()
        # Strip accidental markdown fences (claude sometimes wraps yaml in ```yaml)
        content = re.sub(r"^```ya?ml\s*\n?", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\n?```\s*$", "", content)
        content = content.strip()

        logger.info(
            "Generated GH Actions workflow for %s@%s (%d chars)",
            event.repo_name, event.branch, len(content),
        )
        return GeneratedWorkflow(
            filename=WORKFLOW_FILE_PATH,
            content=content,
            test_file=test_file,
        )
    except AIQuotaExceededError:
        raise
    except Exception as e:
        logger.error("Workflow generation failed: %s", e)
        return None
