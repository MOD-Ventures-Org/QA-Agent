# ARIA QA Agent

Drops into any GitHub repo and, on every push/PR (and after a deployment):
diffs the change, generates tests via an LLM, runs them, and reports failures.
On failure it **fails the check and pings Discord**, so a required status check
holds the merge until the failures are resolved.

> To actually block merges, mark the `aria-qa` check as **required** in the
> repo's branch protection rule for `main`/`master`. Without that, ARIA still
> fails the run and notifies, but GitHub won't hard-block the merge.

## On a successful deployment

When triggered by a `deployment_status` event whose state is **`success`**,
ARIA switches modes: instead of generating and running automated tests, it asks
the LLM for a **product evaluation report** (what changed, risks, what to verify
in the live env) plus **manual test cases** (human-executable steps + expected
results), and posts them to Discord. A **failed** deployment (or a push/PR) keeps
the automated-test behavior above.

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
