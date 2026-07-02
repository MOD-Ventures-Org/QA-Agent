import re
from pathlib import Path

from aria import llm

FRONTEND_EXTENSIONS = {".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss"}
# ponytail: path-hint heuristic, upgrade to manifest-based stack detection if misclassifications show up
FRONTEND_PATH_HINTS = ("component", "page", "view", "frontend", "client", "ui")

FRONTEND_PROMPT = """You are a QA engineer. Generate a single Python Playwright test \
(pytest style, using the `page` fixture from pytest-playwright) that exercises the \
user-facing behavior of this change against the BASE_URL_FRONTEND environment variable.
Output ONLY valid Python code. No markdown fences, no explanation.

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
Output ONLY valid Python code. No markdown fences, no explanation.

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
    match = re.search(r"```(?:python)?\n?(.*?)```", text, re.DOTALL)
    return match.group(1) if match else text


def _build_prompt(file_entry, readme):
    template = FRONTEND_PROMPT if classify(file_entry["path"]) == "frontend" else BACKEND_PROMPT
    return template.format(
        readme=readme or "(no README found)",
        path=file_entry["path"],
        patch=file_entry["patch"],
        content=file_entry["full_content"],
    )


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
        except llm.LLMError as e:
            print(f"aria: skipping {file_entry['path']}: {e}")
            continue

        code = _strip_code_fence(raw)
        try:
            compile(code, "<generated>", "exec")
        except SyntaxError as e:
            print(f"aria: skipping {file_entry['path']}: generated code invalid: {e}")
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", file_entry["path"]).strip("_")
        out_path = output_dir / f"test_gen_{i}_{safe_name}.py"
        out_path.write_text(code)
        generated.append({"path": str(out_path), "source_file": file_entry["path"], "kind": kind})

    return generated
