# ARIA QA Agent — Design Spec

Date: 2026-07-02

## Purpose

A reusable GitHub Actions pipeline that, on every push/PR to any consuming repo:

1. identifies the code that actually changed (git diff),
2. gathers enough repo context to understand what that code does,
3. asks an LLM to generate test cases for the change,
4. runs those tests against the app's running frontend/API,
5. on failure, files one ClickUp ticket per CI run (deduped against existing open tickets) and posts a Discord summary.

It ships as its own repo (`RamaishaRehman/QA-Agent`) so any target repo can adopt it by adding one small caller workflow — no copy-pasted pipeline logic per repo.

## Non-goals

- Not a replacement for hand-written test suites — generated tests are speculative and report-only, they never block a merge.
- Not persisting history across runs (no database). Dedup for tickets is done live against ClickUp, not against stored state.
- Not committing generated tests back into the target repo — they're ephemeral CI artifacts.

## Distribution model: reusable workflow

`QA-Agent` hosts `.github/workflows/qa-pipeline.yml` with `on: workflow_call`, taking the same secrets the current draft workflow already lists (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `KIMI_API_KEY`, `WEBHOOK_SECRET`, `DISCORD_WEBHOOK_URL`, `CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID`, `BASE_URL_FRONTEND`, `BASE_URL_API`, `GITHUB_TOKEN`) as `secrets:` inputs, all but `GITHUB_TOKEN` required.

A consuming repo adds:

```yaml
name: ARIA QA
on:
  pull_request:
    branches: [main, master]
  push:
    branches: [main, master]
jobs:
  aria-qa:
    uses: RamaishaRehman/QA-Agent/.github/workflows/qa-pipeline.yml@main
    secrets: inherit
```

The reusable workflow only needs to check out the *target* repo (`actions/checkout@v4` with `fetch-depth: 0` — required so `git diff` against the previous commit or PR base actually works; the default shallow depth-1 clone breaks this). It does NOT need a second checkout of QA-Agent itself: the reusable workflow's YAML runs on the caller's runner, but the `aria` package's code is fetched separately via `pip install git+https://github.com/RamaishaRehman/QA-Agent@main` (or a published wheel later), so the pipeline logic travels with the pip install, not a checkout step.

`fetch-depth: 0` on the checkout step is a required fix versus the original draft — the default shallow (depth-1) clone makes diffing against the previous commit or PR base impossible.

## Architecture

```
QA-Agent/
├── .github/workflows/qa-pipeline.yml   # the reusable workflow (workflow_call)
├── aria/
│   ├── run_ci_pipeline.py   # orchestrator entrypoint
│   ├── diff.py              # changed-file + patch extraction
│   ├── context.py           # stack detection + full file contents for touched files
│   ├── llm.py                # generate(prompt) -> text; Gemini -> Claude -> Kimi fallback chain
│   ├── testgen.py           # prompt building, frontend/backend classification, writes test files
│   ├── runner.py            # runs generated tests via pytest, collects results
│   ├── clickup.py           # dedup search + create/comment ticket
│   └── discord.py           # posts run summary
├── testing/suites/generated/  # ephemeral output dir, uploaded as a CI artifact
├── tests/                    # tests of the agent itself (not generated tests)
└── requirements.txt
```

Each module is plain functions, no classes/interfaces beyond what's needed for one implementation.

## Data flow

1. `diff.py` reads `GITHUB_EVENT_NAME` and `GITHUB_EVENT_PATH` (both set automatically by the Actions runner — no extra env plumbing needed):
   - `push`: diff `github.event.before` → `GITHUB_SHA`.
   - `pull_request`: diff `github.event.pull_request.base.sha` → head.
   Produces a list of `(file_path, patch, is_new_file)`.
2. `context.py` reads, for each changed file: the full current file content (not just the patch hunk), plus repo-level README and manifest file (`package.json`, `requirements.txt`, `go.mod`, etc.) to detect the stack.
3. `testgen.py` classifies each changed file frontend vs backend by path/extension heuristic (e.g. `.tsx/.jsx/.vue`, `src/components/**` → frontend; `routes/`, `controllers/`, `api/`, `.py` server modules → backend — `ponytail:` heuristic, upgrade to stack-detected routing if it misclassifies often). Builds one prompt per changed file combining diff + full file + stack context, calls `llm.generate()`, writes the resulting test code to `testing/suites/generated/`.
4. `llm.py` tries Gemini, then Claude, then Kimi, returning the first success; raises only if all three fail.
5. `runner.py` runs `pytest` (both Playwright-based and requests/httpx-based generated tests are plain pytest files) against `BASE_URL_FRONTEND`/`BASE_URL_API`, collecting per-test pass/fail + captured output.
6. If any test failed: `clickup.py` searches `CLICKUP_LIST_ID` for an open task tagged with a signature (hash of the run's failing test names) in the task's custom field/tag; if found, adds a comment with this run's details; else creates one new task for the whole run — title = short summary, description = per-test failure list with generated test code + stack trace + link to the Actions run.
7. `discord.py` posts a summary (pass/fail counts, ClickUp link if filed, Actions run link) regardless of outcome.
8. The script always exits 0 (report-only, per the "don't block merges" decision) — the surrounding GitHub Actions job never fails because of generated-test outcomes.

## Error handling

- LLM chain exhausted (Gemini, Claude, and Kimi all error) → log and skip generation for that file; pipeline continues with whatever did generate.
- A generated file that fails to parse as valid Python/pytest → skipped with a warning, doesn't abort the run.
- `CLICKUP_ENABLED=False`, missing `CLICKUP_API_TOKEN`, or missing `BASE_URL_FRONTEND`/`BASE_URL_API` → skip that stage entirely, already partly supported by existing env vars in the draft workflow.
- Any stage's failure (except pytest test failures themselves, which are expected/handled) is logged and reported via Discord rather than crashing the whole job silently.

## Testing (of the agent, not the generated tests)

Each module gets one focused `test_*.py` under `tests/`, using `pytest` with mocked LLM/HTTP calls, covering the real branching logic: diff parsing for both push and PR events, frontend/backend classification, the ClickUp dedup search query, and prompt construction. No fixtures/framework beyond plain pytest unless a specific test needs one.

## Open items resolved during brainstorming

- LLM order: Gemini → Claude → Kimi.
- Test scope: both Playwright (frontend) and API tests (backend), decided per changed file.
- Context strategy: diff + full content of touched files + README/manifest (not full-repo dump, not diff-only).
- Mongo: dropped from scope entirely.
- Ticket dedup: live ClickUp search, no external storage.
- Ticket granularity: one ticket per CI run (not per failing test).
- CI gating: report-only, job always green.
- Test persistence: ephemeral artifacts only, nothing committed back to the target repo.
- Distribution: reusable `workflow_call` workflow, not copy-pasted YAML per repo.
