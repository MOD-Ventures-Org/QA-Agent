import json
import re
from pathlib import Path

from aria import llm

FRONTEND_EXTENSIONS = {".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss"}
# ponytail: path-hint heuristic, upgrade to manifest-based stack detection if misclassifications show up
FRONTEND_PATH_HINTS = ("component", "page", "view", "frontend", "client", "ui")

SUMMARY_MARKER = "===ARIA-SUMMARY==="

_SUMMARY_INSTRUCTIONS = f"""
Respond with exactly two parts, in this order, separated by a line containing only \
{SUMMARY_MARKER}

Part 1 — the Python test code. Output ONLY valid Python code, no markdown fences, no explanation.

Part 2 — a JSON object (no markdown fences) describing the test in plain English, with \
exactly this shape:
{{{{"test_name": "<the test function name>", "purpose": "<one sentence describing what the test verifies>", "steps": ["<step 1>", "<step 2>"], "assertions": ["<what the test checks>"]}}}}
"""

FRONTEND_PROMPT = """You are a QA engineer. Generate a single Python Playwright test \
(pytest style, using the `page` fixture from pytest-playwright) that exercises the \
user-facing behavior of this change against the BASE_URL_FRONTEND environment variable.
""" + _SUMMARY_INSTRUCTIONS + """

Repo README:
{readme}

Changed file: {path}
Diff:
{patch}

Full current file content:
{content}
"""

BACKEND_PROMPT = """You are a QA engineer. Generate a single Python API test \
(pytest style, using the `requests` library) that exercises this change against the \
BASE_URL_API environment variable.
""" + _SUMMARY_INSTRUCTIONS + """

Repo README:
{readme}

Changed file: {path}
Diff:
{patch}

Full current file content:
{content}
"""


def classify(path):
    ext = Path(path).suffix.lower()
    lower = path.lower()
    if ext in FRONTEND_EXTENSIONS or any(hint in lower for hint in FRONTEND_PATH_HINTS):
        return "frontend"
    return "backend"


def _strip_code_fence(text):
    match = re.search(r"```(?:python|json)?\n?(.*?)```", text, re.DOTALL)
    return match.group(1) if match else text


def _build_prompt(file_entry, readme):
    template = FRONTEND_PROMPT if classify(file_entry["path"]) == "frontend" else BACKEND_PROMPT
    return template.format(
        readme=readme or "(no README found)",
        path=file_entry["path"],
        patch=file_entry["patch"],
        content=file_entry["full_content"],
    )


def _extract_test_function_name(code):
    match = re.search(r"^def (test_\w+)", code, re.MULTILINE)
    return match.group(1) if match else None


def _split_code_and_summary(raw):
    if SUMMARY_MARKER in raw:
        code_part, _, summary_part = raw.partition(SUMMARY_MARKER)
        return code_part, summary_part
    return raw, ""


def _parse_summary(summary_text, fallback_name):
    text = _strip_code_fence(summary_text).strip()
    if text:
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
    else:
        data = {}
    return {
        "test_name": data.get("test_name") or fallback_name,
        "purpose": data.get("purpose"),
        "steps": data.get("steps") or [],
        "assertions": data.get("assertions") or [],
    }


def generate_tests(changed_files, repo_context, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    readme = repo_context["repo"]["readme"]

    generated = []
    for i, file_entry in enumerate(changed_files):
        if file_entry.get("full_content") is None:
            continue

        kind = classify(file_entry["path"])
        prompt = _build_prompt(file_entry, readme)

        try:
            raw = llm.generate(prompt)
        except llm.LLMRateLimitError:
            # Provider(s) are rate-limited/out of quota — this isn't a per-file
            # problem, so stop generating entirely and let the caller hold the run.
            raise
        except llm.LLMError as e:
            print(f"aria: skipping {file_entry['path']}: {e}")
            continue

        raw_code, raw_summary = _split_code_and_summary(raw)
        code = _strip_code_fence(raw_code)
        try:
            compile(code, "<generated>", "exec")
        except SyntaxError as e:
            print(f"aria: skipping {file_entry['path']}: generated code invalid: {e}")
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", file_entry["path"]).strip("_")
        out_path = output_dir / f"test_gen_{i}_{safe_name}.py"
        out_path.write_text(code)

        fallback_name = _extract_test_function_name(code) or out_path.stem
        summary = _parse_summary(raw_summary, fallback_name)
        summary_payload = {
            "test_name": summary["test_name"],
            "source_file": file_entry["path"],
            "kind": kind,
            "path": str(out_path),
            "purpose": summary["purpose"],
            "steps": summary["steps"],
            "assertions": summary["assertions"],
        }
        out_path.with_suffix(".json").write_text(json.dumps(summary_payload, indent=2))

        generated.append({
            "path": str(out_path),
            "source_file": file_entry["path"],
            "kind": kind,
            "summary": summary_payload,
        })

    return generated
