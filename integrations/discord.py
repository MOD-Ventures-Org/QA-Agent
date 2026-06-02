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
    evaluation=None,
    run_link: str = "",
) -> dict:
    if result.failed == 0 and result.errors == 0:
        color = COLOR_GREEN
    elif result.passed == 0:
        color = COLOR_RED
    else:
        color = COLOR_AMBER

    failing_names = "\n".join(
        f"• `{f['name']}`" for f in result.failure_details[:5]
    ) or "None"

    latest_commit = (event.commit_messages[-1].splitlines()[0] if event.commit_messages else "N/A")

    fields = [
        {"name": "Repository", "value": event.repo_name, "inline": True},
        {"name": "Branch", "value": event.branch or "N/A", "inline": True},
        {"name": "Event", "value": event.event_type, "inline": True},
        {"name": "Pushed by", "value": event.author or "unknown", "inline": True},
        {"name": "Commit", "value": latest_commit[:200], "inline": False},
        {"name": "Priority", "value": f"{PRIORITY_EMOJI.get(test_plan.priority, '')} {test_plan.priority}", "inline": True},
        {"name": "Test Type", "value": test_plan.test_kind if test_plan.should_test else "none", "inline": True},
        {"name": "Reasoning", "value": test_plan.reasoning[:200], "inline": False},
        {"name": "✅ Passed", "value": str(result.passed), "inline": True},
        {"name": "❌ Failed", "value": str(result.failed), "inline": True},
        {"name": "⚠️ Errors", "value": str(result.errors), "inline": True},
        {"name": "Total / Duration", "value": f"{result.total} / {result.duration:.1f}s", "inline": True},
    ]

    if run_link:
        fields.append({"name": "🔗 View full run", "value": f"[Open dashboard]({run_link})", "inline": False})
    if result.failed > 0 or result.errors > 0:
        fields.append({"name": "Failing / Errored Tests (first 5)", "value": failing_names, "inline": False})
    if bug_summary:
        fields.append({"name": "Bug Summary", "value": bug_summary[:300], "inline": False})

    if evaluation is not None:
        rec_emoji = RECOMMENDATION_EMOJI.get(evaluation.recommendation, "❓")
        strengths = "\n".join(f"• {s}" for s in evaluation.strengths[:4]) or "N/A"
        risks = "\n".join(f"• {r}" for r in evaluation.risks[:4]) or "N/A"
        fields.append({
            "name": "📊 Product Quality",
            "value": f"**{evaluation.grade}** — {evaluation.quality_score}/100  ·  {rec_emoji} {evaluation.recommendation.title()}",
            "inline": False,
        })
        if evaluation.summary:
            fields.append({"name": "Assessment", "value": evaluation.summary[:400], "inline": False})
        fields.append({"name": "✅ Strengths", "value": strengths, "inline": True})
        fields.append({"name": "⚠️ Risks", "value": risks, "inline": True})

    return {
        "title": f"ARIA Report — {event.repo_name} [{event.branch}] #{run_id}",
        "color": color,
        "fields": fields,
        "footer": {
            "text": f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} · ARIA powered by Claude + Playwright"
        },
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


def _build_manual_tests_embed(manual_plan) -> dict:
    lines = []
    for i, case in enumerate(manual_plan.cases[:6], start=1):
        steps = " ".join(f"{n}) {s}" for n, s in enumerate(case.steps, start=1))
        block = f"**{i}. {case.title}**\nSteps: {steps}\nExpected: {case.expected}"
        lines.append(block[:1000])
    description = "\n\n".join(lines) or "No manual cases generated."
    extra = len(manual_plan.cases) - 6
    if extra > 0:
        description += f"\n\n…and {extra} more (see ClickUp)."
    return {
        "title": "📋 Manual Test Cases (for human QA)",
        "color": 0x5865F2,
        "description": description[:4000],
    }


def _build_mongo_embed(run_id: str, result: TestResult, evaluation=None) -> dict:
    if evaluation is not None:
        quality_line = f"{evaluation.quality_score}/100 • {evaluation.grade} • {evaluation.recommendation.title()}"
    else:
        quality_line = "N/A"

    return {
        "title": "💾 MongoDB Report",
        "color": 0x5865F2,
        "fields": [
            {"name": "Run ID", "value": run_id, "inline": True},
            {"name": "Saved to MongoDB", "value": "Yes", "inline": True},
            {"name": "Total tests", "value": str(result.total), "inline": True},
            {"name": "Passed", "value": str(result.passed), "inline": True},
            {"name": "Failed", "value": str(result.failed), "inline": True},
            {"name": "Errors", "value": str(result.errors), "inline": True},
            {"name": "Quality", "value": quality_line, "inline": False},
        ],
    }


async def post_run_started(event: GitHubPushEvent, run_id: str, link: str) -> str:
    """Fire-and-watch ping posted as soon as a pipeline starts, with a link to the
    live dashboard so the run can be followed while it executes."""
    if not settings.discord_enabled:
        logger.info("Discord posting disabled — skipping run-started ping")
        return ""
    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping run-started ping")
        return ""

    latest_commit = (event.commit_messages[-1].splitlines()[0] if event.commit_messages else "N/A")
    embed = {
        "title": f"🚀 ARIA run started — {event.repo_name} [{event.branch}]",
        "color": 0x5865F2,
        "description": f"A QA run has started. Track each step live here:\n**[Open dashboard]({link})**",
        "fields": [
            {"name": "Event", "value": event.event_type, "inline": True},
            {"name": "Pushed by", "value": event.author or "unknown", "inline": True},
            {"name": "Run ID", "value": run_id, "inline": True},
            {"name": "Commit", "value": latest_commit[:200], "inline": False},
        ],
        "footer": {"text": f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} · ARIA"},
    }
    return await _post_embeds([embed])


async def post_discord_report(
    run_id: str,
    event: GitHubPushEvent,
    test_plan: TestPlan,
    result: TestResult,
    bug_summary: str,
    evaluation=None,
    generated_tests=None,
    manual_plan=None,
    mongo_persisted: bool = False,
    run_link: str = "",
) -> str:
    if not settings.discord_enabled:
        logger.info("Discord posting disabled by configuration — skipping Discord post")
        return ""

    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping Discord post")
        return ""

    embed = _build_embed(run_id, event, test_plan, result, bug_summary, evaluation, run_link)
    embeds = [embed]
    if generated_tests is not None:
        embeds.append(_build_generated_tests_embed(generated_tests))
    if manual_plan is not None and getattr(manual_plan, "cases", None):
        embeds.append(_build_manual_tests_embed(manual_plan))
    if mongo_persisted and run_id:
        embeds.append(_build_mongo_embed(run_id, result, evaluation))

    return await _post_embeds(embeds)


async def post_ai_unavailable_report(event: GitHubPushEvent, reason: str) -> str:
    """Post a single concise alert when the AI service is unreachable. No test
    results, bug summary, tickets, or test cases are produced for this run."""
    if not settings.discord_enabled:
        logger.info("Discord posting disabled — skipping AI-unavailable alert")
        return ""
    if not settings.discord_webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping AI-unavailable alert")
        return ""

    latest_commit = (event.commit_messages[-1].splitlines()[0] if event.commit_messages else "N/A")
    embed = {
        "title": "⚠️ ARIA skipped — AI service unavailable",
        "color": COLOR_AMBER,
        "fields": [
            {"name": "Repository", "value": event.repo_name, "inline": True},
            {"name": "Branch", "value": event.branch or "N/A", "inline": True},
            {"name": "Event", "value": event.event_type, "inline": True},
            {"name": "Pushed by", "value": event.author or "unknown", "inline": True},
            {"name": "Commit", "value": latest_commit[:200], "inline": False},
            {"name": "Reason", "value": reason[:300], "inline": False},
            {"name": "Result", "value": "No tests, summaries, tickets, or test cases were generated. Re-run once the AI service is reachable.", "inline": False},
        ],
        "footer": {"text": f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} · ARIA"},
    }
    return await _post_embeds([embed])


async def _post_embeds(embeds: list) -> str:
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
