from unittest.mock import Mock, patch

import pytest

from aria import llm


def _fake_response(json_body, status=200):
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = Mock()
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


def test_generate_uses_gemini_when_it_succeeds(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("KIMI_API_KEY", "k-key")

    gemini_body = {"candidates": [{"content": {"parts": [{"text": "gemini test code"}]}}]}
    with patch("aria.llm.requests.post", return_value=_fake_response(gemini_body)) as post:
        result = llm.generate("write a test")

    assert result == "gemini test code"
    assert post.call_count == 1
    assert "generativelanguage.googleapis.com" in post.call_args[0][0]


def test_generate_falls_back_to_claude_when_gemini_fails(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("KIMI_API_KEY", "k-key")

    claude_body = {"content": [{"text": "claude test code"}]}

    def side_effect(url, **kwargs):
        if "generativelanguage" in url:
            raise Exception("gemini down")
        return _fake_response(claude_body)

    with patch("aria.llm.requests.post", side_effect=side_effect):
        result = llm.generate("write a test")

    assert result == "claude test code"


def test_generate_falls_back_to_kimi_when_gemini_and_claude_fail(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("KIMI_API_KEY", "k-key")

    kimi_body = {"choices": [{"message": {"content": "kimi test code"}}]}

    def side_effect(url, **kwargs):
        if "generativelanguage" in url or "anthropic" in url:
            raise Exception("down")
        return _fake_response(kimi_body)

    with patch("aria.llm.requests.post", side_effect=side_effect):
        result = llm.generate("write a test")

    assert result == "kimi test code"


def test_generate_raises_when_all_providers_fail(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    with pytest.raises(llm.LLMError):
        llm.generate("write a test")
