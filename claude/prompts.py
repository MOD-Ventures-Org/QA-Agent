from webhook.models import GitHubPushEvent


ANALYZER_SYSTEM = """You are a senior QA architect deciding the MINIMAL set of test suites to run
for a specific code change. You are given the repository's README, file tree, the list of changed
files with their contents, who pushed, and the commit messages. Read them and run ONLY what the
change actually requires — do not run suites unrelated to what changed.

Core principle: precision over coverage. If only one API endpoint changed, run the API endpoint
suite (optionally narrowed with a pytest keyword) — not the whole battery. Empty/over-broad plans
waste CI time, which is exactly what we want to avoid.

Guidance:
- Map the changed files to the affected behaviour using the README and file tree for context.
- Enable a suite ONLY if the change plausibly affects what that suite covers.
- Use "pytest_keyword" to narrow execution to the relevant tests (a pytest -k expression, e.g.
  "login or auth"). Leave it "" to run the whole selected suite.
- auth/**, middleware/**, login/session code -> include auth coverage.
- docs/**, *.md, comments only -> set everything false, priority "low".
- release event or PR to main/master -> be more thorough (regression + critical paths), priority "high".
- New feature files with no matching test -> run_generated_tests=true.
- Note: repo-type guardrails are enforced after you respond (a backend repo cannot run UI suites and
  vice-versa), so focus on choosing the smallest correct set; don't worry about cross-type leakage.

Respond ONLY with a JSON object — no markdown, no preamble, no trailing text."""


def _changed_files_section(repo_context) -> str:
    contents = getattr(repo_context, "changed_file_contents", None) or {}
    if not contents:
        return "(file contents unavailable — reason from paths/diff)"
    return "\n\n".join(f"### {path}\n```\n{body}\n```" for path, body in contents.items())


def analyzer_user_prompt(event: GitHubPushEvent, repo_context=None, repo_type: str = "unknown") -> str:
    readme = getattr(repo_context, "readme", "") if repo_context else ""
    file_tree = getattr(repo_context, "file_tree", "") if repo_context else ""

    readme_section = f"\n\n## README (excerpt)\n{readme}" if readme else ""
    tree_section = f"\n\n## File tree\n{file_tree}" if file_tree else ""

    return f"""Event type: {event.event_type}
Repository: {event.repo_name} (detected type: {repo_type})
Branch: {event.branch}
Pushed by: {event.author}
PR title: {event.pr_title or 'N/A'}
Diff summary: {event.diff_summary}

Changed files:
{chr(10).join(f'  - {f}' for f in event.changed_files) or '  (none)'}

Commit messages:
{chr(10).join(f'  - {m}' for m in event.commit_messages) or '  (none)'}{readme_section}{tree_section}

## Changed file contents
{_changed_files_section(repo_context)}

Return exactly this JSON shape:
{{
  "reasoning": "why these suites, tied to what actually changed",
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
  "run_load_tests": bool,
  "pytest_keyword": "pytest -k expression to narrow tests, or empty string",
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


def test_generator_user_prompt(changed_files: list, file_contents: dict, product_context: str = "") -> str:
    files_section = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in file_contents.items()
    )
    context_section = f"\n\n## Product Context\n{product_context}" if product_context else ""
    return f"""Changed/new files that need test coverage:
{chr(10).join(f'  - {f}' for f in changed_files)}

File contents:
{files_section or '(contents not available — use diff context)'}{context_section}

Generate a complete pytest+Playwright test file covering the above changes.
Name each test function clearly so it describes what user behaviour it verifies."""


REPORT_WRITER_SYSTEM = """You are a senior QA engineer writing a plain-English bug report for developers AND
non-technical QA engineers. Use clear everyday language, avoid jargon and stack-trace speak, and write
so anyone on the team can understand what broke. Be concise, precise, and actionable — focus on user
impact and the likely root cause."""


MANUAL_TESTS_SYSTEM = """You are a senior QA engineer writing MANUAL test cases in plain English so a human
QA engineer (non-technical) can execute them by hand against the running product. Based on what changed in
this push, write the specific scenarios a person should click through or check.

Rules:
- Plain, everyday language. No code, no test-function names, no jargon.
- Each case has a short title, concrete numbered steps a human can follow, and a clear expected result.
- Cover the happy path, an important edge case, and an error/invalid case where relevant.
- Only cover what the change actually affects — keep it focused (3 to 7 cases).

Respond ONLY with a JSON object — no markdown, no preamble, no trailing text."""


def manual_tests_user_prompt(event, repo_context=None) -> str:
    changed = "\n".join(f"  - {f}" for f in event.changed_files) or "  (none)"
    commits = "\n".join(f"  - {m}" for m in event.commit_messages) or "  (none)"
    readme = getattr(repo_context, "readme", "") if repo_context else ""
    contents = getattr(repo_context, "changed_file_contents", None) if repo_context else None
    readme_section = f"\n\n## README (excerpt)\n{readme}" if readme else ""
    files_section = ""
    if contents:
        files_section = "\n\n## Changed file contents\n" + "\n\n".join(
            f"### {path}\n```\n{body}\n```" for path, body in contents.items()
        )

    return f"""Repository: {event.repo_name}
Branch: {event.branch}
Event: {event.event_type}
Pushed by: {event.author}

Changed files:
{changed}

Commit messages:
{commits}{readme_section}{files_section}

Write manual test cases a human QA engineer should run for this change.
Return exactly this JSON shape:
{{
  "cases": [
    {{
      "title": "short scenario name in plain English",
      "steps": ["step 1 a person performs", "step 2", "..."],
      "expected": "what the person should see if it works correctly"
    }}
  ]
}}"""


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


EVALUATOR_SYSTEM = """You are a product quality advisor reporting to a business owner, not a developer.
Assess whether the product is ready to ship to real customers. Base your judgment on BOTH:
(1) what the product actually does — read the README and the changed code to understand the feature,
    its purpose, and who it serves; and
(2) the automated test results for this change.
Translate everything into plain business language — focus on user experience and business impact.
Never mention test names, tracebacks, file names, or technical jargon.
Respond ONLY with a JSON object — no markdown, no preamble, no trailing text."""


def evaluator_user_prompt(event, test_plan, test_result, repo_context=None) -> str:
    pass_rate = (test_result.passed / max(test_result.total, 1)) * 100
    failure_text = "\n".join(
        f"  - {f['name']}: {f['error'][:120]}"
        for f in (test_result.failure_details or [])[:8]
    ) or "  (none)"

    readme = getattr(repo_context, "readme", "") if repo_context else ""
    readme_section = f"\n\n## Product README (what the product is and does)\n{readme}" if readme else ""
    code_section = ""
    if repo_context and getattr(repo_context, "changed_file_contents", None):
        code_section = f"\n\n## Code changed in this release\n{_changed_files_section(repo_context)}"

    return f"""Release context: {event.event_type} on {event.repo_name} by {event.author}
PR title: {event.pr_title or 'N/A'}
Affected areas: {', '.join(test_plan.focus_areas) or 'N/A'}
Regression detected: {test_result.regression_detected}

Test outcome summary:
  Pass rate: {pass_rate:.1f}% ({test_result.passed} passed, {test_result.failed} failed out of {test_result.total})
  Errors: {test_result.errors}

Broken areas (internal reference only — translate to user impact in your response):
{failure_text}{readme_section}{code_section}

Assess the product using the README and changed code together with the test outcome.
Return exactly this JSON shape:
{{
  "quality_score": <integer 0-100>,
  "grade": "A" | "B" | "C" | "D" | "F",
  "summary": "<2-3 sentences describing product health in business terms — what users can and cannot do>",
  "strengths": ["<user-facing feature or flow that is working well>", ...],
  "risks": ["<user-facing risk, e.g. 'Users may not be able to reset their password'>", ...],
  "recommendation": "ship" | "ship with caution" | "block"
}}"""
