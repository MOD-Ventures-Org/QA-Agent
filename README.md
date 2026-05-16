# QA Agent

This repository contains **ARIA** (Autonomous Regression & Intelligence Agent) — a fully autonomous QA agent powered by Claude + Playwright.

## Structure

```
QA-Agent/
└── aria/          ← Main application (FastAPI + Claude + Playwright)
    ├── main.py
    ├── config.py
    ├── requirements.txt
    ├── .env.example
    ├── README.md       ← Full how-to-run guide
    ├── claude/         ← AI analysis, test generation, evaluation
    ├── webhook/        ← GitHub webhook receiver
    ├── testing/        ← pytest + Playwright test suites
    ├── storage/        ← MongoDB persistence
    ├── integrations/   ← Discord + ClickUp
    └── utils/          ← Logging
```

See [aria/README.md](aria/README.md) for the full setup and run guide. hello

