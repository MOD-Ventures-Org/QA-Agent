from webhook.models import GitHubPushEvent


ANALYZER_SYSTEM = """You are a senior QA architect. Analyze the GitHub event context and return a JSON test plan.
Decide which suites to run based on file paths changed.

Decision rules:
- *.tsx, *.jsx, *.css, *.html, src/components/** → enable UI suites + accessibility
- routes/**, controllers/**, api/**, *.py server files → enable API suites
- auth/**, middleware/** → always enable run_api_auth=true and run_ui_critical_paths=true
- docs/**, *.md only → set all to false, priority=low
- release event → all true, priority=critical
- PR to main or master → all regression + critical paths, priority=high
- New feature files detected (no corresponding test file) → run_generated_tests=true

Respond ONLY with a JSON object — no markdown, no preamble, no trailing text."""


def analyzer_user_prompt(event: GitHubPushEvent) -> str:
    return f"""Event type: {event.event_type}
Repository: {event.repo_name}
Branch: {event.branch}
Author: {event.author}
PR title: {event.pr_title or 'N/A'}
Diff summary: {event.diff_summary}

Changed files:
{chr(10).join(f'  - {f}' for f in event.changed_files) or '  (none)'}

Commit messages:
{chr(10).join(f'  - {m}' for m in event.commit_messages) or '  (none)'}

Return exactly this JSON shape:
{{
  "reasoning": "short explanation",
  "run_ui_smoke": bool,
  "run_ui_regression": bool,
  "run_ui_critical_paths": bool,
  "run_api_endpoints": bool,
  "run_api_auth": bool,
  "run_api_contracts": bool,
  "run_functional_integration": bool,
  "run_functional_edge_cases": bool,
  "run_accessibility": bool,
  "run_generated_tests": bool,
  "priority": "critical" | "high" | "medium" | "low",
  "focus_areas": ["list of areas most at risk"],
  "affected_pages": ["list of page URLs/routes affected"]
}}"""


TEST_GENERATOR_SYSTEM = """You are a senior QA engineer. Generate a complete, runnable pytest+Playwright test file
for the changed feature described. The file must:
- Import fixtures from conftest.py (page, base_url, api_client)
- Use @pytest.mark.ui or @pytest.mark.api markers appropriately
- Cover happy paths, edge cases, and error states
- Be immediately executable with no modifications
Output only valid Python code — no markdown fences, no explanation."""


def test_generator_user_prompt(changed_files: list, file_contents: dict) -> str:
    files_section = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in file_contents.items()
    )
    return f"""Changed/new files that need test coverage:
{chr(10).join(f'  - {f}' for f in changed_files)}

File contents:
{files_section or '(contents not available — use diff context)'}

Generate a complete pytest+Playwright test file covering the above changes."""


REPORT_WRITER_SYSTEM = """You are a senior QA engineer writing a plain-English bug report for developers.
Be concise, precise, and actionable. Avoid jargon. Focus on impact and likely root cause."""


def report_writer_user_prompt(test_plan_reasoning: str, failures: list) -> str:
    failure_text = "\n\n".join(
        f"Test: {f.get('name')}\nError: {f.get('error')}\nTraceback: {f.get('traceback', '')[:500]}"
        for f in failures
    )
    return f"""QA analysis context: {test_plan_reasoning}

Failing tests:
{failure_text}

Write a plain-English bug summary covering:
1. What broke and which user flows are affected
2. Likely root cause
3. Severity assessment
4. Suggested fix direction

Keep it under 400 words."""


EVALUATOR_SYSTEM = """You are a senior QA director performing a post-run product quality evaluation.
Assess overall product health and release readiness based on test results, failure patterns, and change context.
Respond ONLY with a JSON object — no markdown, no preamble, no trailing text."""


def evaluator_user_prompt(event, test_plan, test_result) -> str:
    pass_rate = (test_result.passed / max(test_result.total, 1)) * 100
    failure_text = "\n".join(
        f"  - {f['name']}: {f['error'][:120]}"
        for f in (test_result.failure_details or [])[:8]
    ) or "  (none)"

    return f"""Event: {event.event_type} on {event.repo_name} [{event.branch}] by {event.author}
PR title: {event.pr_title or 'N/A'}
Priority: {test_plan.priority}
Focus areas: {', '.join(test_plan.focus_areas) or 'N/A'}
Regression detected: {test_result.regression_detected}

Test Results:
  Total: {test_result.total}
  Passed: {test_result.passed} ({pass_rate:.1f}%)
  Failed: {test_result.failed}
  Errors: {test_result.errors}
  Duration: {test_result.duration:.1f}s

Failing tests:
{failure_text}

Return exactly this JSON shape:
{{
  "quality_score": <integer 0-100>,
  "grade": "A" | "B" | "C" | "D" | "F",
  "summary": "<2-3 sentence overall product health assessment>",
  "strengths": ["<what is working well>", ...],
  "risks": ["<key risk or concern>", ...],
  "recommendation": "ship" | "ship with caution" | "block"
}}"""
