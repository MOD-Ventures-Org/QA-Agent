from openai import OpenAI

from config import settings
from utils.logger import get_logger
from claude.analyzer import TestPlan
from claude.prompts import REPORT_WRITER_SYSTEM, report_writer_user_prompt

logger = get_logger(__name__)
client = OpenAI(api_key=settings.openai_api_key)


async def write_bug_report(test_plan: TestPlan, test_result) -> str:
    failures = test_result.failure_details or []
    if not failures:
        return "No failures to report."
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1024,
            temperature=0,
            messages=[
                {"role": "system", "content": REPORT_WRITER_SYSTEM},
                {"role": "user", "content": report_writer_user_prompt(test_plan.reasoning, failures)},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Report writer failed: {e}")
        return f"Bug report generation failed: {e}"
