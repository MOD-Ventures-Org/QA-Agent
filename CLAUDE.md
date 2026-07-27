# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
python -m pytest tests/ -q                                  # run all tests
python -m pytest tests/test_discord.py -q                   # run one file
python -m pytest tests/test_llm.py::test_name -q            # run one test
pip install -e .                                            # install locally (or: pip install -r requirements.txt)
```

Tests mock all network calls (`unittest.mock.patch` on `requests`) and all git/env access, so they run anywhere with no credentials.

## What this is

ARIA is an LLM-driven QA agent that runs inside **other repos'** GitHub Actions. Consumers copy `examples/caller-workflow.yml` into their repo; it calls the reusable workflow `.github/workflows/qa-pipeline.yml` here, which pip-installs this package from git and runs `python -m aria.run_ci_pipeline`. There is no long-running service — everything is a single CI invocation driven by GitHub Actions environment variables (`GITHUB_EVENT_NAME`, `GITHUB_EVENT_PATH`, `GITHUB_WORKSPACE`, ...). Running the pipeline locally requires faking those.

## Pipeline flow

`aria/run_ci_pipeline.py:main()` orchestrates everything; the other modules are stages:

1. **diff.py** — resolves base/head SHAs per event type (`pull_request`, `push`, `deployment_status` diffs the deployed commit against its parent) and returns changed files with patches via `git diff`.
2. **context.py** — attaches each file's full content plus repo README/manifests.
3. Branch on event:
   - **Failed deployment** (`deployment_status` with state `failure`/`error` — Vercel and DigitalOcean report deploys this way): only a simple Discord notification (provider detected from the event payload); no diffing, tests, or tickets — exit 0.
   - **Push / PR / successful deployment**: **testgen.py** generates one pytest per changed file — Playwright for frontend files, `requests`-based API tests for backend (classified by extension/path heuristics). Each LLM response is code + a JSON summary separated by `===ARIA-SUMMARY===`; code is validated with `compile()` before writing to `testing/suites/generated/` alongside a `.json` summary.
4. **runner.py** — runs the generated tests via pytest `--junitxml` and parses the XML (never raises on test failure).
5. **clickup.py** — on failures, files a ticket tagged `bug`, deduplicated by a `[aria-sig:<sha>]` marker computed from the failing test names; an existing open ticket gets a comment instead of a duplicate.
6. **discord.py** — posts the run report (created/run/passed/failed counts, details of only the failed tests, ClickUp link) via webhook, chunked under the 2000-char message limit; every message overrides the display name with `USERNAME`.
7. Exit code 1 on failures so a required `aria-qa` status check holds the merge (reporting happens before the exit).

## Conventions and behaviors to preserve

- **LLM fallback chain** (`llm.py`): Gemini → Claude → Kimi. HTTP 429 raises `LLMRateLimitError`, which aborts the whole run with **exit 0** ("hold, retry later") — deliberately not a failure. Other `LLMError`s just skip that file.
- **Feature flags**: Discord/ClickUp integration is gated on `DISCORD_ENABLED` / `CLICKUP_ENABLED` env vars holding the literal string `"True"` — set automatically by `qa-pipeline.yml` based on whether the corresponding secrets exist.
- **Graceful degradation everywhere**: missing webhook URL, no changed files, no generated tests, or LLM failure on the evaluation path all exit 0 without raising. Only real test failures exit 1.
- `# ponytail:` comments mark known heuristics with a planned upgrade path — keep the marker when touching that code.
- Design/plan docs live in `docs/superpowers/` (spec and implementation plan for the original build).
