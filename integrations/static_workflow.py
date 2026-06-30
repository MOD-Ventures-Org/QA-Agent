"""The single, generic GitHub Actions workflow ARIA commits to a target repo.

Unlike the old per-event ``workflow_generator`` (which asked Claude to rewrite the
YAML on every push), this file is static: ARIA pushes it once, and GitHub re-runs it
on every PR / push / deployment with no regeneration. It runs whatever tests ARIA has
committed under ``testing/suites/generated/``, uploads the JSON report as an artifact,
and POSTs the report back to ARIA's ``/webhook/results`` endpoint — where the
reporting brain (Discord, ClickUp, eval report, dashboard) takes over.

Dedup / "don't run every time" is handled here, not by regenerating:
  * ``paths:``        — only fire when source or generated tests actually change
  * ``concurrency:``  — cancel superseded runs on the same ref (the synchronize storm)
  * ``if:`` label     — let a PR opt out with the ``aria-skip`` label

Target repo must define two secrets: ARIA_CALLBACK_URL and ARIA_CALLBACK_TOKEN.
"""

STATIC_WORKFLOW_PATH = ".github/workflows/aria_generated_tests.yml"

# NOTE: kept as a plain string (no .format) so GitHub's ${{ }} expressions survive verbatim.
STATIC_WORKFLOW_CONTENT = r"""# Managed by ARIA. Committed once; do not hand-edit — ARIA may overwrite it.
name: ARIA Generated Tests

on:
  pull_request:
    branches: [main, master]
    paths:
      - "**/*.py"
      - "**/*.js"
      - "**/*.ts"
      - "**/*.tsx"
      - "testing/suites/generated/**"
  push:
    branches: [main, master]
    paths:
      - "**/*.py"
      - "**/*.js"
      - "**/*.ts"
      - "**/*.tsx"
      - "testing/suites/generated/**"
  deployment_status:

concurrency:
  group: aria-${{ github.ref }}
  cancel-in-progress: true

jobs:
  run-tests:
    runs-on: ubuntu-latest
    # Opt out on a single PR by adding the "aria-skip" label.
    if: >-
      github.event_name != 'pull_request' ||
      !contains(join(github.event.pull_request.labels.*.name, ','), 'aria-skip')
    env:
      # URLs / flags the generated tests + conftest read. Set these as repo secrets
      # (Settings -> Secrets and variables -> Actions) for tests that hit a live app.
      BASE_URL_FRONTEND: ${{ secrets.BASE_URL_FRONTEND }}
      BASE_URL_API: ${{ secrets.BASE_URL_API }}
      PLAYWRIGHT_HEADLESS: "true"
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install pytest pytest-json-report pytest-timeout httpx
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

      - name: Install Playwright (only if UI tests are present)
        run: |
          if grep -rqiE 'playwright|mark\.ui|\(.*\bpage\b' testing/suites/generated/ 2>/dev/null; then
            echo "UI/Playwright tests detected — installing browser."
            pip install pytest-playwright
            playwright install --with-deps chromium
          else
            echo "No UI/Playwright tests detected — skipping browser install."
          fi

      - name: Run ARIA-generated tests
        id: tests
        continue-on-error: true
        run: |
          if [ -d testing/suites/generated ] && [ -n "$(ls -A testing/suites/generated 2>/dev/null)" ]; then
            pytest testing/suites/generated/ -v --timeout=120 --tb=short \
              --json-report --json-report-file=aria_report.json
          else
            echo "No generated tests yet — emitting empty report."
            echo '{"summary":{"total":0,"passed":0,"failed":0,"error":0},"tests":[],"duration":0}' > aria_report.json
          fi

      - name: Upload report artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: aria-test-report
          path: aria_report.json

      - name: Send results to ARIA
        if: always()
        env:
          ARIA_CALLBACK_URL: ${{ secrets.ARIA_CALLBACK_URL }}
          ARIA_CALLBACK_TOKEN: ${{ secrets.ARIA_CALLBACK_TOKEN }}
        run: |
          if [ -z "$ARIA_CALLBACK_URL" ]; then
            echo "ARIA_CALLBACK_URL not set — skipping callback."; exit 0
          fi
          if [ ! -f aria_report.json ]; then
            echo '{"summary":{"total":0,"passed":0,"failed":0,"error":0},"tests":[],"duration":0}' > aria_report.json
          fi
          jq -n \
            --arg repo "$GITHUB_REPOSITORY" \
            --arg branch "$GITHUB_REF_NAME" \
            --arg event "$GITHUB_EVENT_NAME" \
            --arg sha "$GITHUB_SHA" \
            --arg actor "$GITHUB_ACTOR" \
            --arg run_url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
            --slurpfile report aria_report.json \
            '{repo:$repo,branch:$branch,event:$event,sha:$sha,actor:$actor,run_url:$run_url,report:$report[0]}' \
          | curl -sS -X POST "$ARIA_CALLBACK_URL/webhook/results" \
              -H "Content-Type: application/json" \
              -H "X-Aria-Token: $ARIA_CALLBACK_TOKEN" \
              --data-binary @-

      - name: Fail job if tests failed
        if: steps.tests.outcome == 'failure'
        run: exit 1
"""
