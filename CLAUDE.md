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
```

## Architecture

**ARIA** is a FastAPI webhook server that listens for GitHub events, uses Claude AI to analyze the change, generates customized tests and a GitHub Actions workflow, then pushes both back to the branch so GitHub Actions executes them. Results appear in the GitHub Actions tab — there is no local test execution.

### Pipeline flow (`webhook/router.py → _run_pipeline`)

Each accepted GitHub event triggers this sequence in a background task:

1. **ai_check** — DNS check that `api.anthropic.com` is reachable before doing anything
2. **clone** — shallow-clones the pushed repo to a temp dir (`claude/repo_context.py`); skips clone in CI when `GITHUB_WORKSPACE` is set
3. **analyze** — Claude reads the README, file tree, and changed files to produce a `TestPlan` (`should_test`, `test_kind`, `focus_areas`, `priority`)
4. **generate** — Claude writes pytest/Playwright test code specific to the change → `testing/suites/generated/`
5. **generate_workflow** — Claude writes `.github/workflows/aria_generated_tests.yml` tailored to this repo's runtime and test kind (`claude/workflow_generator.py`)
6. **push_to_repo** — pushes the test file + workflow to the branch via GitHub Contents API (`integrations/github_push.py`); GitHub Actions picks them up automatically

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

### Generated workflow

`claude/workflow_generator.py` asks Claude to produce a minimal `.github/workflows/aria_generated_tests.yml`. The prompt provides repo type, test kind, focus areas, README, and file tree. Claude is instructed to:
- Only add `pip install` / `npm install` steps if the corresponding dependency file exists in the file tree
- Add `playwright install chromium` only for `ui` or `mixed` test kinds
- Run: `pytest testing/suites/generated/{test_file} -v --timeout=120 --tb=short --json-report --json-report-file=aria_report.json`
- Upload `aria_report.json` as artifact `aria-test-report`

Accidental markdown fences are stripped from Claude's output with regex before the YAML is pushed.

### CI mode (`scripts/run_ci_pipeline.py`)

Reads `GITHUB_EVENT_NAME` and `GITHUB_EVENT_PATH` from the environment, calls `_extract_event` / `_run_pipeline_safe` directly — bypasses the HTTP server entirely. The workflow at `.github/workflows/aria.yml` installs ARIA from `RamaishaRehman/QA-Agent` into a target repo's workflow and invokes this script.

### Storage / dashboard (retained but not used by the active pipeline)

`storage/runs.py`, `api/router.py`, and `frontend/` still exist and serve a live dashboard at `/ui`. The current pipeline does **not** write to the `runs` collection — these are available for future use. `storage/mongo.py` contains `save_pipeline_output` for writing consolidated JSON to `pipeline_outputs`.

## Key configuration (`.env`)

`WEBHOOK_SECRET` is required (no default — server won't start without it).

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude AI (primary provider) |
| `KIMI_API_KEY` | Kimi/Moonshot (fallback provider) |
| `GITHUB_TOKEN` | Cloning private repos **and** pushing generated files back (requires `contents: write`) |
| `WEBHOOK_SECRET` | GitHub webhook HMAC signature validation |
| `DISCORD_ENABLED` / `DISCORD_WEBHOOK_URL` | Discord integration (not used by the active pipeline) |
| `CLICKUP_ENABLED` / `CLICKUP_API_TOKEN` / `CLICKUP_LIST_ID` | ClickUp integration (not used by the active pipeline) |
| `MONGODB_URI` | Defaults to `mongodb://localhost:27017` |
| `MONGODB_DB_NAME` | Defaults to `aria` |
| `BASE_URL_FRONTEND` / `BASE_URL_API` | URLs of the app under test (passed to generated tests) |
| `PLAYWRIGHT_HEADLESS` | `True` in CI, `False` for local debugging |
| `NGROK_AUTHTOKEN` | Auto-opens a public tunnel at startup |
| `PUBLIC_BASE_URL` | Overrides ngrok; sets the base URL used in run links |
| `LOAD_TEST_*` | Load test thresholds: `REQUESTS`, `CONCURRENCY`, `PATH`, `MAX_P95_MS`, `MIN_SUCCESS_RATE` |

## Tests (`tests/`)

`tests/test_flows.py` contains happy/sad flow integration tests that mock MongoDB, Discord, and AI calls. These run without any live services and are separate from the AI-generated suites in `testing/suites/generated/`.
