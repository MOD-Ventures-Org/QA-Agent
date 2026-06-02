# ARIA — Autonomous Regression & Intelligence Agent

ARIA is a fully autonomous QA agent for your GitHub repos. When code is **merged** or **deployed**, it clones the repo, reads the actual change, **generates tests specific to that change**, and runs them with pytest + Playwright — then reports results to Discord, files bug tickets, and produces a plain-English manual test checklist for human QA. ARIA does **not** run a fixed battery of pre-written suites; every test it runs is generated for the change in front of it.

It uses a dual-provider AI client: **Anthropic Claude** is primary, with **Kimi** as an automatic fallback if Claude is unavailable.

## How It Works

1. **GitHub webhook** arrives. ARIA only runs the pipeline for:
   - a **merged pull request** (into any branch),
   - a **push to `main`/`master`**, or
   - a **`deployment` / `deployment_status` / `release`** event.

   Pull-request-opened/synchronize, reviews, and feature-branch pushes are **skipped**.
2. **Repo context** — ARIA shallow-clones the pushed repo/branch and extracts the `README`, file tree, and the contents of the changed files. It detects whether the repo is **backend** or **frontend** from the name and file signals (`package.json` → frontend, `pyproject.toml`/`requirements.txt` → backend).
3. **Claude analyzes the change** — using that context, Claude decides **whether the change is worth testing** (`should_test`) and **what kind of tests fit** (`test_kind`: `ui`, `api`, `functional`, or `mixed`), plus the focus areas, affected pages, priority, and an optional `pytest -k` keyword. Docs-only/comment changes are marked not worth testing.
   - **Test kind follows the change:** a frontend change yields browser (UI) tests; a backend change yields API tests; a change spanning both yields `mixed`. When Claude omits the kind, it's inferred from the repo type.
   - **Fallback:** if Claude can't produce a plan, ARIA assumes the change is testable and lets the generator decide what to write.
   - **Deployments** generate a smoke validation pass.
4. **Test generation** — Claude generates a fresh pytest test file targeting the specific change, using the right tooling for the test kind (Playwright `page` for UI, `httpx` `api_client` for API), covering happy path, edge cases, and error states.
5. **Execution** — pytest runs the generated tests (honoring the `-k` filter) and captures a screenshot on any UI failure. If a change isn't worth testing, generation/execution are skipped but the report is still posted.
6. **Manual test cases** — Claude writes plain-English, step-by-step manual test cases so a human QA engineer can verify the change by hand.
7. **Persistence** — results, screenshots, and the manual test cases are stored in MongoDB.
8. **Reporting** — Discord gets a rich embed showing **repository, branch, event, who pushed, commit message**, pass/fail counts, the bug summary, and the manual test cases.
9. **Tickets (only when there are bugs)** — if any test fails, ARIA files a ClickUp ticket per failure **and** a manual-QA checklist ticket. On a clean run, **no tickets are created**.

## Generated Tests

ARIA generates one pytest file per change into `testing/suites/generated/`, choosing the test kind from what changed:

| `test_kind` | Generated for | Tooling |
|---|---|---|
| `ui` | frontend / page / component changes | Playwright browser tests via the `page` fixture against `BASE_URL_FRONTEND` |
| `api` | backend endpoint / service / data changes | `httpx` calls via the `api_client` fixture against `BASE_URL_API` |
| `functional` | business logic / integration spanning units | whichever fixtures fit |
| `mixed` | changes touching both frontend and backend | UI + API tests in one file |

Shared fixtures (`page`, `base_url`, `api_client`, `api_base_url`) live in `testing/suites/conftest.py`. Generated UI tests auto-capture a screenshot on failure.

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
# Run the tests ARIA generated for the latest change
python -m pytest testing/suites/generated/ -v

# Narrow with a keyword (same mechanism ARIA uses)
python -m pytest testing/suites/generated/ -k "auth or login" -v

# With JSON report (same mechanism ARIA uses internally)
python -m pytest testing/suites/generated/ --json-report --json-report-file=report.json -v

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
| `BASE_URL_FRONTEND` | Frontend URL for generated Playwright UI tests |
| `BASE_URL_API` | API base URL for generated httpx API tests |
| `PLAYWRIGHT_HEADLESS` | `True` for headless mode (default: `True`) |

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
├── testing/                # pytest runner + regression watcher
│   └── suites/
│       ├── conftest.py     # shared fixtures: page, base_url, api_client, api_base_url
│       └── generated/      # Claude-generated, change-specific tests (auto-created)
├── storage/                # MongoDB persistence
├── integrations/           # Discord + ClickUp
├── tests/                  # ARIA's own unit tests
└── utils/                  # Logging
```
