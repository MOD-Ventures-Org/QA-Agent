# ARIA QA Agent

Drops into any GitHub repo and, on every push/PR: diffs the change, generates
tests via an LLM, runs them, and reports failures — report-only, never blocks
a merge.

## Setup

1. Copy `examples/caller-workflow.yml` into your repo as
   `.github/workflows/aria-qa.yml`.
2. In your repo's secrets, set:
   - `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `KIMI_API_KEY` (required — LLM
     fallback chain tries Gemini, then Claude, then Kimi)
   - `BASE_URL_FRONTEND`, `BASE_URL_API` (optional — where generated tests
     run against; skip either to skip that test category)
   - `CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID` (optional — omit to skip ticket
     filing entirely)
   - `DISCORD_WEBHOOK_URL` (optional — omit to skip notifications)
3. Push or open a PR. Generated tests land as a `generated-tests` artifact
   on the run regardless of pass/fail.
