from aria import llm

EVAL_PROMPT = """You are a senior QA analyst reviewing a change that was just \
successfully deployed to a live environment. Based on the repository context and \
the deployed changes below, produce a Markdown document with exactly this section \
and nothing else:

## Manual Test Cases
A numbered list of manual, human-executable test cases covering the deployed change.
For each case include: a title, preconditions, numbered steps, and the expected result.

Write only the Markdown document. Do not wrap the whole document in code fences.

Repository README:
{readme}

Deployed changes:
{changes}
"""


def _format_changes(changed_files):
    blocks = []
    for entry in changed_files:
        blocks.append(
            "File: {path} ({status})\n{patch}".format(
                path=entry["path"],
                status=entry.get("status", "?"),
                patch=entry.get("patch", ""),
            )
        )
    return "\n\n".join(blocks)


def generate_evaluation(changed_files, repo_context):
    """Ask the LLM for manual test cases (Markdown) covering a successfully
    deployed change. Raises llm.LLMError on failure."""
    readme = repo_context["repo"]["readme"] or "(no README found)"
    prompt = EVAL_PROMPT.format(readme=readme, changes=_format_changes(changed_files))
    return llm.generate(prompt)
