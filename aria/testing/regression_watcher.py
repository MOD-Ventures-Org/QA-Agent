from utils.logger import get_logger
from webhook.models import GitHubPushEvent
from testing.result_parser import TestResult

logger = get_logger(__name__)


async def check_regression(event: GitHubPushEvent, result: TestResult) -> TestResult:
    try:
        from storage.mongo import get_recent_runs
        recent = await get_recent_runs(event.repo_name, event.branch, limit=5)
        if not recent:
            return result

        avg_failure_rate = sum(
            r.get("failed", 0) / max(r.get("total", 1), 1) for r in recent
        ) / len(recent)

        current_rate = result.failed / max(result.total, 1)
        if current_rate > avg_failure_rate + 0.10:
            logger.warning(
                f"Regression detected: current failure rate={current_rate:.2%} "
                f"vs baseline={avg_failure_rate:.2%}"
            )
            result.regression_detected = True
    except Exception as e:
        logger.error(f"Regression check failed: {e}")
    return result
