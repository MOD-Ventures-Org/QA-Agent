from claude.client import DualAIClient

from config import settings
from utils.logger import get_logger
from claude.analyzer import TestPlan
from claude.prompts import REPORT_WRITER_SYSTEM, report_writer_user_prompt

logger = get_logger(__name__)
client = DualAIClient(settings.anthropic_api_key, settings.gemini_api_key)


async def write_bug_report(test_plan: TestPlan, test_result) -> str:
    failures = test_result.failure_details or []
    if not failures:
        return "No failures to report."
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            temperature=0,
            system=REPORT_WRITER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": report_writer_user_prompt(test_plan.reasoning, failures),
                }
            ],
        )
        return message.content[0].text.strip()
    except Exception as e:
        logger.error(f"Report writer failed: {e}")
        return f"Bug report generation failed: {e}"
