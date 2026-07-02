from webhook.models import GitHubPushEvent


ANALYZER_SYSTEM = """You are a senior QA architect. ARIA does NOT run pre-written test suites — it GENERATES
tests specific to the change that was just pushed, then runs them with pytest + Playwright. Your job is
to decide (1) whether this change is worth generating tests for, and (2) what kind of tests fit and where
to focus them. You are given the repository's README, file tree, the changed files with their contents,
who pushed, and the commit messages.

Decide:
- should_test: true if the change touches real product behaviour (UI, API, business logic). false for
  docs-only, comments, formatting, config that doesn't affect behaviour, or pure dependency bumps.
- test_kind: the type of generated tests that best fits WHAT CHANGED:
    "ui"         -> frontend/page/component changes (run in a real browser via Playwright).
    "api"        -> backend endpoint/service/data changes (HTTP calls against the API).
    "functional" -> business logic / integration spanning multiple units.
    "mixed"      -> the change spans both frontend and backend.
  Match this to the change and the repo type (a backend change cannot be UI-tested, and vice-versa).
- focus_areas: the behaviours/areas most at risk from this change (e.g. "authentication", "checkout").
- affected_pages: page URLs/routes affected (for UI changes); empty list otherwise.
- pytest_keyword: an optional pytest -k expression to narrow the generated tests (e.g. "login or auth").
  Leave it "" to run all generated tests.
- priority: "critical" | "high" | "medium" | "low". Release / PR-to-main / auth / payment changes are high+.

Guidance:
- Map the changed files to the affected behaviour using the README and file tree for context.
- auth/**, middleware/**, login/session code -> focus on authentication, high priority.
- docs/**, *.md, comments-only -> should_test=false, priority "low".

Respond ONLY with a JSON object — no markdown, no preamble, no trailing text."""


def _changed_files_section(repo_context) -> str:
    contents = getattr(repo_context, "changed_file_contents", None) or {}
    if not contents:
        return "(file contents unavailable — reason from paths/diff)"
    return "\n\n".join(f"### {path}\n```\n{body}\n```" for path, body in contents.items())


def analyzer_user_prompt(event: GitHubPushEvent, repo_context=None, repo_type: str = "unknown") -> str:
    readme = getattr(repo_context, "readme", "") if repo_context else ""
    claude_md = getattr(repo_context, "claude_md", "") if repo_context else ""
    file_tree = getattr(repo_context, "file_tree", "") if repo_context else ""

    readme_section = f"\n\n## README (excerpt)\n{readme}" if readme else ""
    conventions_section = (
        f"\n\n## Repo conventions (from CLAUDE.md — follow these)\n{claude_md}" if claude_md else ""
    )
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
{chr(10).join(f'  - {m}' for m in event.commit_messages) or '  (none)'}{readme_section}{conventions_section}{tree_section}

## Changed file contents
{_changed_files_section(repo_context)}

Return exactly this JSON shape:
{{
  "reasoning": "what to test and why, tied to what actually changed",
  "should_test": bool,
  "test_kind": "ui" | "api" | "functional" | "mixed",
  "pytest_keyword": "pytest -k expression to narrow tests, or empty string",
  "priority": "critical" | "high" | "medium" | "low",
  "focus_areas": ["list of areas most at risk"],
  "affected_pages": ["list of page URLs/routes affected"]
}}"""


TEST_GENERATOR_SYSTEM = """You are a senior QA engineer. Generate ONE complete, runnable pytest test file that
targets the SPECIFIC code change described — not generic smoke tests. The file must:
- Import fixtures from conftest.py. Available fixtures: page (Playwright browser page), base_url
  (frontend URL), api_client (httpx.Client), api_base_url (API URL).
- Choose the right tooling for the test_kind you are given:
    "ui"         -> drive the browser with the `page` fixture against `base_url`; mark @pytest.mark.ui.
    "api"        -> call endpoints with the `api_client` fixture against `api_base_url`; mark @pytest.mark.api.
    "functional" -> exercise the business logic / integration, using whichever fixtures fit.
    "mixed"      -> combine UI and API tests in the same file with the appropriate markers.
- For UI (Playwright) tests, unless the Product Context or repo conventions say otherwise:
    * Prefer resilient locators in this order: get_by_role(name=...), get_by_label(...),
      get_by_text(...), get_by_test_id(...). Avoid brittle CSS/XPath, nth-child, and class selectors.
    * Assert with Playwright's auto-waiting API: `from playwright.sync_api import expect` and
      `expect(locator).to_be_visible()` / `expect(page).to_have_url(...)`. Never use time.sleep().
    * Navigate with page.goto(f"{base_url}/path"); reproduce the login flow from the Product Context
      before hitting protected pages.
- If a "UI Test Conventions" / selector / login section is present in the Product Context or repo
  conventions, follow it EXACTLY (selectors, test-id attribute, auth steps) — it overrides the defaults above.
- Cover the happy path, important edge cases, and error/invalid states for the changed behaviour.
- Focus on the focus_areas and affected_pages provided.
- Be immediately executable with no modifications.
Output only valid Python code — no markdown fences, no explanation."""


def test_generator_user_prompt(
    changed_files: list,
    file_contents: dict,
    product_context: str = "",
    repo_type: str = "unknown",
    test_kind: str = "mixed",
    focus_areas: list | None = None,
    affected_pages: list | None = None,
    conventions: str = "",
) -> str:
    files_section = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in file_contents.items()
    )
    context_section = f"\n\n## Product Context\n{product_context}" if product_context else ""
    conventions_section = (
        f"\n\n## Repo conventions (from CLAUDE.md — follow these when writing tests)\n{conventions}"
        if conventions else ""
    )
    focus = ", ".join(focus_areas or []) or "N/A"
    pages = ", ".join(affected_pages or []) or "N/A"
    return f"""Repository type: {repo_type}
Test kind to generate: {test_kind}
Focus areas (most at risk): {focus}
Affected pages/routes: {pages}

Changed/new files that need test coverage:
{chr(10).join(f'  - {f}' for f in changed_files)}

File contents:
{files_section or '(contents not available — use diff context)'}{context_section}{conventions_section}

Generate a complete {test_kind} test file covering the above changes for this {repo_type} repository.
Name each test function clearly so it describes what user behaviour it verifies."""


REPORT_WRITER_SYSTEM = """You are a senior QA engineer writing plain-English bug reports for developers AND
non-technical QA engineers. Use clear everyday language, avoid jargon and stack-trace speak, and write
so anyone on the team can understand what broke. Be concise, precise, and actionable — focus on user
impact and the likely root cause.

For EACH failing test, write a short plain-English title describing what feature or flow is broken
(no test function names, no file paths, no code) and a 1-3 sentence plain-English description of what
broke, the likely cause, and the user impact. Then write one overall summary covering all the failures
together.

Respond ONLY with a JSON object — no markdown, no preamble, no trailing text."""


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

Write a plain-English bug report. Return exactly this JSON shape:
{{
  "summary": "<overall summary under 400 words covering: what broke and which user flows are
    affected, likely root cause, severity assessment, and suggested fix direction>",
  "items": [
    {{
      "test_name": "<exact test name copied from above, so this item can be matched to its test>",
      "title": "<short plain-English title for what's broken — no test names, file paths, or code>",
      "description": "<1-3 plain-English sentences: what broke, likely cause, and user impact>"
    }}
  ]
}}
Include exactly one item per failing test listed above, in the same order."""


WORKFLOW_GENERATOR_SYSTEM = """You are a DevOps engineer. Generate a minimal, correct GitHub Actions workflow YAML that runs AI-generated pytest tests in the target repository.

Rules:
- The workflow name MUST be exactly: "ARIA Generated Tests"
- Runner: ubuntu-latest, Python 3.11.
- Only include install steps for dependency files that are PRESENT in the file tree (requirements.txt → pip install; package.json → npm install). Do not add steps for files that don't exist.
- For test_kind "ui" or "mixed" (frontend): include "playwright install chromium --with-deps".
- Test command: pytest {TEST_FILE_PATH} -v --timeout=120 --tb=short --json-report --json-report-file=aria_report.json
- Add an upload step using actions/upload-artifact@v4 with name "aria-test-report" pointing to aria_report.json.
- Do NOT add steps or tools that are not clearly needed.
- Respond with ONLY valid YAML — no markdown fences, no explanation."""


def workflow_generator_user_prompt(event, test_plan, test_file: str, repo_context=None) -> str:
    repo_type = getattr(repo_context, "repo_type", "unknown") if repo_context else "unknown"
    readme = (getattr(repo_context, "readme", "") or "")[:1500]
    file_tree = (getattr(repo_context, "file_tree", "") or "")[:1000]
    focus = ", ".join(test_plan.focus_areas) if test_plan.focus_areas else "general"

    return f"""Repository: {event.repo_name}
Branch: {event.branch}
Repo type: {repo_type}
Test kind: {test_plan.test_kind}
Generated test file (relative to repo root): testing/suites/generated/{test_file}
Focus areas: {focus}
Priority: {test_plan.priority}

README (truncated):
{readme or '(not available)'}

File tree (truncated):
{file_tree or '(not available)'}

Generate the GitHub Actions workflow YAML. The pytest command must point to: testing/suites/generated/{test_file}"""


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
