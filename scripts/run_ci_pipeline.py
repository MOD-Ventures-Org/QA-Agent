"""
Runs the ARIA pipeline directly from a GitHub Actions event payload,
bypassing the webhook server entirely. GITHUB_WORKSPACE is already set
by actions/checkout so the clone step is skipped automatically.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webhook.router import _extract_event, _should_process_event, _run_pipeline_safe


async def main():
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")

    if not event_name or not event_path:
        print("ERROR: GITHUB_EVENT_NAME or GITHUB_EVENT_PATH not set — not running in GitHub Actions")
        sys.exit(1)

    with open(event_path) as f:
        payload = json.load(f)

    event = _extract_event(event_name, payload)

    if not _should_process_event(event):
        print(f"Skipping: event={event_name} repo={event.repo_name} branch={event.branch!r} — does not meet pipeline criteria")
        sys.exit(0)

    print(f"Starting ARIA pipeline: event={event_name} repo={event.repo_name} branch={event.branch!r}")
    await _run_pipeline_safe(event)
    print("ARIA pipeline complete")


if __name__ == "__main__":
    asyncio.run(main())
