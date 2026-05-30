# ARIA — Autonomous Regression & Intelligence Agent

ARIA is a fully autonomous QA agent for your GitHub repos. When code is **merged** or **deployed**, it clones the repo, reads the actual change, and runs **only the test suites that change requires** — then reports results to Discord, files bug tickets, and produces a plain-English manual test checklist for human QA.

It uses a dual-provider AI client: **Anthropic Claude** is primary, with **Kimi** as an automatic fallback if Claude is unavailable.

## How It Works

1. **GitHub webhook** arrives. ARIA only runs the pipeline for:
   - a **merged pull request** (into any branch),
   - a **push to `main`/`master`**, or
   - a **`deployment` / `deployment_status` / `release`** event.

   Pull-request-opened/synchronize, reviews, and feature-branch pushes are **skipped**.
2. **Repo context** — ARIA shallow-clones the pushed repo/branch and extracts the `README`, file tree, and the contents of the changed files. It detects whether the repo is **backend** or **frontend** from the name and file signals (`package.json` → frontend, `pyproject.toml`/`requirements.txt` → backend).
3. **Claude plans the run** — using that context, Claude picks the **minimal** set of suites the change actually needs, and may narrow execution with a `pytest -k` keyword. The goal is precision, not running everything.
   - **Guardrails:** a backend repo never runs UI suites; a frontend repo never runs API/load suites.
   - **Fallback:** if Claude can't produce a plan, ARIA runs a minimal **smoke** set (guardrail-trimmed), not the whole battery.
   - **Deployments** run a full validation pass.
4. **Test generation** — for new features, Claude generates fresh pytest+Playwright tests.
5. **Execution** — Playwright/pytest runs the selected suites (UI, API, functional, accessibility, **load**), honoring the `-k` filter.
6. **Manual test cases** — Claude writes plain-English, step-by-step manual test cases so a human QA engineer can verify the change by hand.
7. **Persistence** — results, screenshots, and the manual test cases are stored in MongoDB.
8. **Reporting** — Discord gets a rich embed showing **repository, branch, event, who pushed, commit message**, pass/fail counts, the bug summary, and the manual test cases.
9. **Tickets (only when there are bugs)** — if any test fails, ARIA files a ClickUp ticket per failure **and** a manual-QA checklist ticket. On a clean run, **no tickets are created**.

## Test Suites

| Suite | Runs for | Location |
|---|---|---|
| UI (smoke / regression / critical paths) | frontend changes | `testing/suites/ui/` |
| Functional (integration / edge cases) | either | `testing/suites/functional/` |
| Accessibility (axe-core) | frontend changes | `testing/suites/accessibility/` |
| API (endpoints / auth / contracts) | backend changes | `testing/suites/api/` |
| **Load** (concurrent requests, p95 + success-rate thresholds) | backend changes | `testing/suites/load/` |
| Generated | new features | `testing/suites/generated/` |

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/MOD-Ventures-Org/QA-Agent.git
cd QA-Agent
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in the values in .env
```

### 3. Start MongoDB

```bash
# Local Docker
docker run -d -p 27017:27017 mongo:7

# Or use a MongoDB Atlas URI in .env
```

### 4. Run ARIA locally

```bash
python main.py
```

ARIA starts on `http://localhost:8000` and opens an ngrok tunnel. Copy the tunnel URL shown in the terminal and paste it into your GitHub repository's webhook settings:

- **Payload URL:** `https://xxxx.ngrok.io/webhook/github`
- **Content type:** `application/json`
- **Secret:** value of `WEBHOOK_SECRET` in your `.env`
- **Events:** Pushes, Pull requests, Deployments, Releases

### 5. Verify

```
GET http://localhost:8000/health
```

Returns `{"status": "ok", "tunnel": "https://xxxx.ngrok.io"}`.

## Running Tests Manually

```bash
# All suites
python -m pytest testing/suites/ -v

# Specific suite
python -m pytest testing/suites/ui/ -v
python -m pytest testing/suites/api/ -v
python -m pytest testing/suites/load/ -v
python -m pytest testing/suites/accessibility/ -v

# Narrow with a keyword (same mechanism ARIA uses)
python -m pytest testing/suites/api/ -k "auth or login" -v

# With JSON report
python -m pytest testing/suites/ --json-report --json-report-file=report.json -v

# The agent's own unit tests
python -m pytest tests/ -v
```

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Claude API key (primary AI provider) |
| `KIMI_API_KEY` | Kimi API key (fallback provider) |
| `KIMI_MODEL` | Kimi model name (default `kimi-1.0`) |
| `KIMI_API_URL` | Kimi API base URL |
| `GITHUB_TOKEN` | Token used to clone the pushed repo (required for private repos) |
| `WEBHOOK_SECRET` | Secret for validating GitHub webhook signatures |
| `DISCORD_WEBHOOK_URL` | Discord incoming webhook URL |
| `DISCORD_ENABLED` | Toggle Discord posting (default `False`) |
| `CLICKUP_ENABLED` | Toggle ClickUp ticket creation (default `False`) |
| `CLICKUP_API_TOKEN` | ClickUp API token |
| `CLICKUP_LIST_ID` | ClickUp list ID for tickets |
| `NGROK_AUTHTOKEN` | Ngrok auth token (local dev only) |
| `MONGODB_URI` | MongoDB connection string |
| `MONGODB_DB_NAME` | MongoDB database name (default: `aria`) |
| `BASE_URL_FRONTEND` | Frontend URL for Playwright tests |
| `BASE_URL_API` | API base URL for httpx/load tests |
| `PLAYWRIGHT_HEADLESS` | `True` for headless mode (default: `True`) |

**Load test tuning** (optional, sensible defaults): `LOAD_TEST_REQUESTS` (50), `LOAD_TEST_CONCURRENCY` (10), `LOAD_TEST_PATH` (`/health`), `LOAD_TEST_MAX_P95_MS` (1000), `LOAD_TEST_MIN_SUCCESS_RATE` (0.95).

> `ANTHROPIC_API_KEY` is the primary provider; `KIMI_API_KEY` enables the fallback. Set both for resilience.

## GitHub Actions CI

The pipeline can run headlessly in CI. Add these secrets to your GitHub repo (`Settings → Secrets → Actions`):

- `ANTHROPIC_API_KEY`
- `KIMI_API_KEY`
- `GITHUB_TOKEN`
- `DISCORD_WEBHOOK_URL`
- `CLICKUP_API_TOKEN`
- `WEBHOOK_SECRET`
- `BASE_URL_FRONTEND`
- `BASE_URL_API`

## Project Structure

```
QA-Agent/
├── main.py                 # FastAPI app + ngrok startup
├── config.py               # Settings from .env
├── requirements.txt
├── .env.example
├── webhook/                # GitHub webhook receiver, signature validation, trigger gate
├── claude/                 # AI: analyzer, repo_context (clone), test_generator,
│                           #     manual_tests, report_writer, evaluator, client, prompts
├── testing/                # pytest runner + test suites
│   └── suites/
│       ├── ui/             # Playwright UI tests
│       ├── api/            # API contract tests
│       ├── functional/     # Integration & edge case tests
│       ├── accessibility/  # axe-core a11y tests
│       ├── load/           # Concurrent load tests (httpx)
│       └── generated/      # Claude-generated tests (auto-created)
├── storage/                # MongoDB persistence
├── integrations/           # Discord + ClickUp
├── tests/                  # ARIA's own unit tests
└── utils/                  # Logging
```
