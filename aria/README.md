# ARIA — Autonomous Regression & Intelligence Agent

ARIA is a fully autonomous QA agent that runs on every GitHub push or PR. It uses Claude to analyze changes, generate tests, execute them with Playwright, and report results to Discord and ClickUp.

## How It Works

1. GitHub sends a webhook on every push/PR
2. Claude reads the diff and decides which test suites to run
3. Claude generates new pytest+Playwright tests for changed features
4. Playwright executes all selected tests (UI, API, functional, accessibility)
5. Results + screenshots are stored in MongoDB
6. Claude writes a plain-English bug summary
7. Discord receives a rich embed report
8. ClickUp gets a bug ticket for every failing test

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/RamaishaRehman/QA-Agent.git
cd QA-Agent/aria
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in all values in .env
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
- **Secret:** value of `GITHUB_WEBHOOK_SECRET` in your `.env`
- **Events:** Push, Pull requests

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
python -m pytest testing/suites/accessibility/ -v

# With JSON report
python -m pytest testing/suites/ --json-report --json-report-file=report.json -v
```

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GITHUB_WEBHOOK_SECRET` | Secret for validating GitHub webhooks |
| `DISCORD_WEBHOOK_URL` | Discord incoming webhook URL |
| `NGROK_AUTHTOKEN` | Ngrok auth token (local dev only) |
| `CLICKUP_API_TOKEN` | ClickUp API token |
| `CLICKUP_LIST_ID` | ClickUp list ID for bug tickets |
| `MONGODB_URI` | MongoDB connection string |
| `MONGODB_DB_NAME` | MongoDB database name (default: `aria`) |
| `BASE_URL_FRONTEND` | Frontend URL for Playwright tests |
| `BASE_URL_API` | API base URL for httpx tests |
| `PLAYWRIGHT_HEADLESS` | `True` for headless mode (default: `True`) |

## GitHub Actions CI

Push to any branch to trigger the pipeline automatically. Add these secrets to your GitHub repo (`Settings → Secrets → Actions`):

- `ANTHROPIC_API_KEY`
- `DISCORD_WEBHOOK_URL`
- `CLICKUP_API_TOKEN`
- `GITHUB_WEBHOOK_SECRET`
- `BASE_URL_FRONTEND`
- `BASE_URL_API`

## Project Structure

```
aria/
├── main.py                 # FastAPI app + ngrok startup
├── config.py               # Settings from .env
├── requirements.txt
├── .env.example
├── webhook/                # GitHub webhook receiver
├── claude/                 # AI analysis, test generation, reporting
├── testing/                # pytest runner + test suites
│   └── suites/
│       ├── ui/             # Playwright UI tests
│       ├── api/            # API contract tests
│       ├── functional/     # Integration & edge case tests
│       ├── accessibility/  # axe-core a11y tests
│       └── generated/      # Claude-generated tests (auto-created)
├── storage/                # MongoDB persistence
├── integrations/           # Discord + ClickUp
└── utils/                  # Logging
```
all code help taken from claude vs code extension
