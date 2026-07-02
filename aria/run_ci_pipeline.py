import os
import sys

from aria import clickup, context, diff, discord, runner, testgen

OUTPUT_DIR = "testing/suites/generated"


def _run_url():
    return "{}/{}/actions/runs/{}".format(
        os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    )


def main():
    repo_dir = os.environ.get("GITHUB_WORKSPACE", ".")

    changed = diff.get_changed_files(repo_dir=repo_dir)
    if not changed:
        print("aria: no changed files, nothing to do")
        return 0

    ctx = context.build_context(changed, repo_dir=repo_dir)
    generated = testgen.generate_tests(changed, ctx, OUTPUT_DIR)
    if not generated:
        print("aria: no tests generated")
        return 0

    results = runner.run_tests(OUTPUT_DIR)
    run_url = _run_url()

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
        )

    print(f"aria: passed={results['passed']} failed={results['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
