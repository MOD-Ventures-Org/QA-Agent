# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browser (required for UI tests)
playwright install chromium

# Start the server (port 8000)
uvicorn main:app --reload --port 8000
# or
python main.py

# Run integration/flow tests (mock-based, no live services needed)
pytest tests/ -v

# Run a single test
pytest tests/test_flows.py::<test_name> -v
```

## Architecture

**ARIA** is a hybrid QA agent. The **generation half** (FastAPI webhook server) clones the pushed repo, uses Claude to generate change-specific tests, and commits them plus a generic static workflow back to the branch. GitHub Actions runs those tests **in the runner** and POSTs the JSON report back to ARIA's `/webhook/results` endpoint. The **reporting half** (the "brain") turns that report into a product evaluation, manual test cases, Discord notifications, ClickUp tickets, MongoDB persistence, and a dashboard run.

This keeps per-event test *execution* in the cloud (cheap, no infra) while ARIA only regenerates tests when the diff is genuinely new, and the static workflow is committed once instead of regenerated every event.

### Generation pipeline (`webhook/router.py → _run_pipeline`)

Each accepted GitHub event triggers this sequence in a background task:

1. **ai_check** — DNS check that `api.anthropic.com` is reachable before doing anything
2. **clone** — shallow-clones the pushed repo to a temp dir (`claude/repo_context.py`); skips clone in CI when `GITHUB_WORKSPACE` is set
3. **fingerprint check** — hashes `(repo, branch, changed files + contents)` (`storage/fingerprints.py`); if this exact diff was already generated, skip the AI calls entirely (the committed tests still re-run in Actions)
4. **analyze** — Claude reads the README, file tree, and changed files to produce a `TestPlan`
5. **generate** — Claude writes pytest/Playwright test code specific to the change → `testing/suites/generated/`
6. **push + mark** — pushes the test file + the static workflow (`integrations/static_workflow.py`) via the GitHub Contents API (idempotent — skips unchanged files), then records the fingerprint

The per-event workflow generator (`claude/workflow_generator.py`) is retained but no longer called by the pipeline; the workflow is now static.

This half **creates the dashboard `runs` record** and drives its first steps live — `clone → analyze → generate → push` — recording each step's output (repo details, the analyzer `TestPlan`, the generated test file + names + code, the pushed files). It stores the commit `sha` on the run and leaves the `actions` step *running* as it hands off to GitHub Actions. The reporting half later attaches to this same run (see below), so one dashboard run shows the whole story end-to-end.

### Unified run timeline

`storage/models.RUN_STEPS` is one 11-step timeline spanning both halves: `clone, analyze, generate, push` (generation) → `actions` (hand-off) → `parse, evaluate, bug_report, manual, notify, persist` (reporting). The generation half creates the run; the reporting half correlates its callback to that run via `runs.find_open_run(repo, branch, sha)` — exact `sha` match first, else the most recent still-`running` run for that repo/branch. If nothing matches (identical-diff skip, or a repo using CI mode), the reporting half creates a fresh run and marks the generation steps `skipped`.

### Reporting brain (`webhook/results.py`, mounted at `/webhook/results`)

The static workflow POSTs `{repo, branch, event, sha, actor, run_url, report}` back here (auth: `X-Aria-Token` vs `ARIA_CALLBACK_TOKEN`). `process_results` **attaches to the generation run** (`find_open_run`), closes the `actions` step, then parses the pytest report and runs: `evaluate_product` → `write_bug_report` → `generate_manual_tests` → Discord post → ClickUp tickets → MongoDB persistence → final dashboard `runs` patch. Each AI call degrades gracefully (quota errors are logged, not fatal) and marks its step `skipped`.

`repo_context.cleanup()` always runs in a `finally` block. MongoDB errors are swallowed and logged — they never abort the pipeline.

### Infinite loop prevention

When ARIA pushes files to the repo, GitHub sends a `push` event back. `_should_process_event` checks commit messages for `[skip aria]` first; ARIA always includes this marker in its own commits so the resulting push is ignored.

### Events processed

Only these GitHub events enter the pipeline; everything else returns `status: skipped`:
- `push` to `main` or `master`
- `pull_request` — only `opened`, `synchronize`, `reopened` actions targeting main/master
- `deployment_status` — only when `state == "success"`

### AI client (`claude/client.py`)

`DualAIClient` calls Claude (Anthropic) first, falls back to Kimi (Moonshot) if Claude fails. `AIQuotaExceededError` is raised (and caught by `_run_pipeline_safe`) when all providers hit quota/rate limits — logs only, no re-raise.

Each AI module (`analyzer`, `workflow_generator`, `test_generator`, `evaluator`, `manual_tests`, `report_writer`) instantiates its own `DualAIClient` at module load time using `config.settings`.

### Static workflow (`integrations/static_workflow.py`)

A single generic `.github/workflows/aria_generated_tests.yml`, committed once. It runs everything in `testing/suites/generated/`, uploads `aria_report.json` as the `aria-test-report` artifact, and POSTs the report to `$ARIA_CALLBACK_URL/webhook/results`. Dedup ("don't run every time") lives here:
- `paths:` — only fire when source files or generated tests change
- `concurrency: cancel-in-progress` — supersede stale runs on the same ref (the PR `synchronize` storm)
- `if:` — a PR can opt out with the `aria-skip` label

Target repos must define two secrets: `ARIA_CALLBACK_URL` (ARIA's public/ngrok URL) and `ARIA_CALLBACK_TOKEN` (must match ARIA's `ARIA_CALLBACK_TOKEN`).

### CI mode (`scripts/run_ci_pipeline.py`)

Reads `GITHUB_EVENT_NAME` and `GITHUB_EVENT_PATH` from the environment, calls `_extract_event` / `_run_pipeline_safe` directly — bypasses the HTTP server entirely. The template at `templates/aria.yml` installs ARIA from `RamaishaRehman/QA-Agent` into a target repo's workflow and invokes this script. **This template must be copied into a target repo's `.github/workflows/` — it is deliberately kept out of ARIA's own `.github/workflows/`, because GitHub runs a workflow against whatever repo it lives in (placing it here made ARIA test itself instead of the target repo).**

### Storage / dashboard

`storage/runs.py`, `api/router.py`, and `frontend/` serve a live dashboard at `/ui`. The `runs` collection (per-run dashboard record) is **created by the generation pipeline and patched by the reporting brain** — one unified record per event (see "Unified run timeline"). The **reporting brain** (`webhook/results.py`) additionally writes `test_runs`, `bug_reports`, `manual_tests`, the raw runner output in `ci_reports` (`save_ci_report` — the exact `aria_report.json` + CI metadata), and the consolidated `pipeline_outputs` via `storage/mongo.py`. The generation pipeline writes `test_fingerprints` (`storage/fingerprints.py`). The dashboard run-detail view renders the 11-step timeline with per-step outputs, the generated test code, pass/fail results, evaluation, bug summary, and a link to the GitHub Actions run.

## Key configuration (`.env`)

`WEBHOOK_SECRET` is required (no default — server won't start without it).

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude AI (primary provider) |
| `KIMI_API_KEY` | Kimi/Moonshot (fallback provider) |
| `GITHUB_TOKEN` | Cloning private repos **and** pushing generated files back (requires `contents: write`) |
| `WEBHOOK_SECRET` | GitHub webhook HMAC signature validation |
| `ARIA_CALLBACK_TOKEN` | Shared secret verifying `/webhook/results` callbacks (must match the target repo's `ARIA_CALLBACK_TOKEN` secret) |
| `DISCORD_ENABLED` / `DISCORD_WEBHOOK_URL` | Discord integration — called by the reporting brain but no-ops unless `DISCORD_ENABLED` is true |
| `CLICKUP_ENABLED` / `CLICKUP_API_TOKEN` / `CLICKUP_LIST_ID` | ClickUp integration — called by the reporting brain but no-ops unless `CLICKUP_ENABLED` is true |
| `MONGODB_URI` | Defaults to `mongodb://localhost:27017` |
| `MONGODB_DB_NAME` | Defaults to `aria` |
| `BASE_URL_FRONTEND` / `BASE_URL_API` | URLs of the app under test (passed to generated tests) |
| `PLAYWRIGHT_HEADLESS` | `True` in CI, `False` for local debugging |
| `NGROK_AUTHTOKEN` | Auto-opens a public tunnel at startup |
| `PUBLIC_BASE_URL` | Overrides ngrok; sets the base URL used in run links |
| `LOAD_TEST_*` | Load test thresholds: `REQUESTS`, `CONCURRENCY`, `PATH`, `MAX_P95_MS`, `MIN_SUCCESS_RATE` |

## Tests (`tests/`)

`tests/test_flows.py` contains happy/sad flow integration tests that mock MongoDB, Discord, and AI calls. These run without any live services and are separate from the AI-generated suites in `testing/suites/generated/`.
