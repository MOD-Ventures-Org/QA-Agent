import asyncio
from datetime import datetime, timezone

import httpx

from config import settings
from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from claude.analyzer import TestPlan
from testing.result_parser import TestResult

logger = get_logger(__name__)

COLOR_GREEN = 0x27500A
COLOR_RED = 0xA32D2D
COLOR_AMBER = 0x854F0B

PRIORITY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}

GRADE_COLOR = {"A": 0x1B6B2F, "B": 0x4A7C2F, "C": 0xB8860B, "D": 0xB85C00, "F": 0xA32D2D}
RECOMMENDATION_EMOJI = {"ship": "✅", "ship with caution": "⚠️", "block": "🚫"}


def _build_embed(
    run_id: str,
    event: GitHubPushEvent,
    test_plan: TestPlan,
    result: TestResult,
    bug_summary: str,
) -> dict:
    if result.failed == 0 and result.errors == 0:
        color = COLOR_GREEN
    elif result.passed == 0:
        color = COLOR_RED
    else:
        color = COLOR_AMBER

    suites_run = sum(
        1 for flag in [
            test_plan.run_ui_smoke, test_plan.run_ui_regression, test_plan.run_ui_critical_paths,
            test_plan.run_api_endpoints, test_plan.run_api_auth, test_plan.run_api_contracts,
            test_plan.run_functional_integration, test_plan.run_functional_edge_cases,
            test_plan.run_accessibility,
        ] if flag
    )

    failing_names = "\n".join(
        f"• `{f['name']}`" for f in result.failure_details[:5]
    ) or "None"

    fields = [
        {"name": "Event", "value": event.event_type, "inline": True},
        {"name": "Priority", "value": f"{PRIORITY_EMOJI.get(test_plan.priority, '')} {test_plan.priority}", "inline": True},
        {"name": "Suites Run", "value": str(suites_run), "inline": True},
        {"name": "Reasoning", "value": test_plan.reasoning[:200], "inline": False},
        {"name": "✅ Passed", "value": str(result.passed), "inline": True},
        {"name": "❌ Failed", "value": str(result.failed), "inline": True},
        {"name": "Total / Duration", "value": f"{result.total} / {result.duration:.1f}s", "inline": True},
        {"name": "Regression Detected", "value": "⚠️ Yes" if result.regression_detected else "No", "inline": True},
    ]

    if result.failed > 0:
        fields.append({"name": "Failing Tests (first 5)", "value": failing_names, "inline": False})
    if bug_summary:
        fields.append({"name": "Bug Summary", "value": bug_summary[:300], "inline": False})

    return {
        "title": f"ARIA Report — {event.repo_name} [{event.branch}] #{run_id}",
        "color": color,
        "fields": fields,
        "footer": {
            "text": f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} · ARIA powered by Claude + Playwright"
        },
    }


def _build_evaluation_embed(evaluation) -> dict:
    color = GRADE_COLOR.get(evaluation.grade, COLOR_AMBER)
    rec_emoji = RECOMMENDATION_EMOJI.get(evaluation.recommendation, "❓")

    strengths = "\n".join(f"• {s}" for s in evaluation.strengths[:4]) or "N/A"
    risks = "\n".join(f"• {r}" for r in evaluation.risks[:4]) or "N/A"

    return {
        "title": "📊 Product Evaluation",
        "color": color,
        "fields": [
            {"name": "Grade", "value": f"**{evaluation.grade}** — {evaluation.quality_score}/100", "inline": True},
            {"name": "Recommendation", "value": f"{rec_emoji} {evaluation.recommendation.title()}", "inline": True},
            {"name": "Summary", "value": evaluation.summary[:300] or "N/A", "inline": False},
            {"name": "✅ Strengths", "value": strengths, "inline": True},
            {"name": "⚠️ Risks", "value": risks, "inline": True},
        ],
    }


def _build_generated_tests_embed(summary) -> dict:
    test_list = "\n".join(f"• `{name}`" for name in summary.test_names[:15]) or "No test functions found"
    triggered = "\n".join(f"• `{f}`" for f in summary.triggered_by) or "N/A"
    return {
        "title": "🧪 Generated Test Cases",
        "color": 0x5865F2,
        "fields": [
            {"name": "File", "value": f"`{summary.file_name}`", "inline": True},
            {"name": "Tests Generated", "value": str(len(summary.test_names)), "inline": True},
            {"name": "Triggered By", "value": triggered, "inline": False},
            {"name": "Test Cases", "value": test_list, "inline": False},
        ],
    }


async def post_discord_report(
    run_id: str,
    event: GitHubPushEvent,
    test_plan: TestPlan,
    result: TestResult,
    bug_summary: str,
    evaluation=None,
    generated_tests=None,
) -> str:
    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping Discord post")
        return ""

    embed = _build_embed(run_id, event, test_plan, result, bug_summary)
    embeds = [embed]
    if generated_tests is not None:
        embeds.append(_build_generated_tests_embed(generated_tests))
    if evaluation is not None:
        embeds.append(_build_evaluation_embed(evaluation))
    payload = {"embeds": embeds}

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    settings.discord_webhook_url,
                    json=payload,
                    params={"wait": "true"},
                )
                if response.status_code == 429:
                    logger.warning(f"Discord rate limited (attempt {attempt + 1}), retrying in 2s")
                    await asyncio.sleep(2)
                    continue
                response.raise_for_status()
                data = response.json()
                message_id = str(data.get("id", ""))
                logger.info(f"Discord message posted id={message_id}")
                return message_id
        except Exception as e:
            logger.error(f"Discord post attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(2)

    return ""
