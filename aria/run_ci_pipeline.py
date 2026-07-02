import json
import os
import sys

from aria import clickup, context, diff, discord, evaluate, llm, runner, testgen

OUTPUT_DIR = "testing/suites/generated"


def _run_url():
    return "{}/{}/actions/runs/{}".format(
        os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    )


def _trigger_info():
    """Human-readable description of what triggered this run, for the Discord
    summary — e.g. "push", "pull_request", or "deployment (failure) · env: prod"."""
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "deployment_status":
        try:
            with open(os.environ["GITHUB_EVENT_PATH"]) as f:
                event = json.load(f)
        except (KeyError, OSError, ValueError):
            return "deployment"
        state = event.get("deployment_status", {}).get("state", "unknown")
        env = event.get("deployment", {}).get("environment")
        label = f"deployment ({state})"
        if env:
            label += f" · env: {env}"
        return label
    return event_name or None


def _is_successful_deployment():
    if os.environ.get("GITHUB_EVENT_NAME") != "deployment_status":
        return False
    try:
        with open(os.environ["GITHUB_EVENT_PATH"]) as f:
            event = json.load(f)
    except (KeyError, OSError, ValueError):
        return False
    return event.get("deployment_status", {}).get("state") == "success"


def _run_evaluation(changed, ctx, run_url):
    """Successful-deployment path: produce a product evaluation report + manual
    test cases and send them to Discord instead of running automated tests."""
    try:
        report = evaluate.generate_evaluation(changed, ctx)
    except llm.LLMError as e:
        print(f"aria: could not generate evaluation: {e}")
        return 0

    print("aria: product evaluation report\n" + report)
    if os.environ.get("DISCORD_ENABLED", "False") == "True":
        discord.post_evaluation(
            os.environ.get("DISCORD_WEBHOOK_URL"),
            report, run_url, trigger=_trigger_info(),
        )
    return 0


def main():
    repo_dir = os.environ.get("GITHUB_WORKSPACE", ".")

    changed = diff.get_changed_files(repo_dir=repo_dir)
    if not changed:
        print("aria: no changed files, nothing to do")
        return 0

    ctx = context.build_context(changed, repo_dir=repo_dir)
    run_url = _run_url()

    if _is_successful_deployment():
        return _run_evaluation(changed, ctx, run_url)

    generated = testgen.generate_tests(changed, ctx, OUTPUT_DIR)
    if not generated:
        print("aria: no tests generated")
        return 0

    results = runner.run_tests(OUTPUT_DIR)

    ticket_url = None
    if results["failed"] > 0 and os.environ.get("CLICKUP_ENABLED", "False") == "True":
        list_id = os.environ.get("CLICKUP_LIST_ID")
        token = os.environ.get("CLICKUP_API_TOKEN")
        if list_id and token:
            task_id = clickup.file_ticket_for_run(list_id, token, results["failures"], run_url)
            ticket_url = f"https://app.clickup.com/t/{task_id}"

    if os.environ.get("DISCORD_ENABLED", "False") == "True":
        discord.post_summary(
            os.environ.get("DISCORD_WEBHOOK_URL"),
            results["passed"], results["failed"], run_url, ticket_url,
            trigger=_trigger_info(),
        )

    print(f"aria: passed={results['passed']} failed={results['failed']}")
    if results["failed"] > 0:
        # Fail the check so a required status check holds the merge. Reporting
        # (ClickUp + Discord) has already run above, so notifications still fire.
        print("aria: tests failed — failing the check to hold the merge")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
