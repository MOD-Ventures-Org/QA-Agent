import os

import requests


class LLMError(Exception):
    pass


class LLMRateLimitError(LLMError):
    """Raised when a provider fails specifically because its rate limit/quota
    was hit (HTTP 429), as opposed to misconfiguration or an outage."""
    pass


def _raise_for_status(resp):
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        if resp.status_code == 429:
            raise LLMRateLimitError(str(e)) from e
        raise


def _call_gemini(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("GEMINI_API_KEY not set")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-flash-latest:generateContent?key={api_key}"
    )
    resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
    _raise_for_status(resp)
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_claude(prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY not set")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-5",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    _raise_for_status(resp)
    data = resp.json()
    return data["content"][0]["text"]


def _call_kimi(prompt):
    api_key = os.environ.get("KIMI_API_KEY")
    if not api_key:
        raise LLMError("KIMI_API_KEY not set")
    resp = requests.post(
        "https://api.moonshot.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "moonshot-v1-32k",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    _raise_for_status(resp)
    data = resp.json()
    return data["choices"][0]["message"]["content"]


_PROVIDERS = [_call_gemini, _call_claude, _call_kimi]


def generate(prompt):
    errors = []
    rate_limited = False
    for provider in _PROVIDERS:
        try:
            return provider(prompt)
        except LLMRateLimitError as e:
            rate_limited = True
            errors.append(f"{provider.__name__}: {e}")
        except Exception as e:
            errors.append(f"{provider.__name__}: {e}")

    message = "all LLM providers failed: " + "; ".join(errors)
    if rate_limited:
        raise LLMRateLimitError(message)
    raise LLMError(message)
