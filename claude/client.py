import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import anthropic
import httpx
from anthropic import Anthropic

logger = logging.getLogger(__name__)


class AIQuotaExceededError(RuntimeError):
    """Raised when every configured AI provider reports a token/usage/quota limit
    error (rate limit, billing/credit issue, or context-length exceeded)."""


_QUOTA_ERROR_TERMS = (
    "rate_limit", "rate limit", "quota", "credit balance", "insufficient_quota",
    "context_length_exceeded", "maximum context length", "usage limit",
    "too many requests", "overloaded_error", "billing issue", "429", "529",
)


def _is_quota_error(exc: Exception) -> bool:
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if getattr(exc, "status_code", None) in (429, 529):
        return True
    text = str(exc).lower()
    return any(term in text for term in _QUOTA_ERROR_TERMS)


class DualAIResponse:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(text=text)]


class DualMessagesProxy:
    def __init__(self, client: "DualAIClient"):
        self._client = client

    def create(
        self,
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0,
        system: str = "",
        messages: List[Dict[str, str]] = [],
    ) -> DualAIResponse:
        return self._client.create(model=model, max_tokens=max_tokens, temperature=temperature, system=system, messages=messages)


class DualAIClient:
    def __init__(
        self,
        anthropic_api_key: str = "",
        kimi_api_key: str = "",
        kimi_model: str = "moonshot-v1-8k",
        kimi_api_url: str = "https://api.moonshot.ai/v1/chat/completions",
    ):
        self.anthropic_client: Optional[Anthropic] = None
        if anthropic_api_key:
            self.anthropic_client = Anthropic(api_key=anthropic_api_key)

        self.kimi_api_key = kimi_api_key
        self.kimi_model = kimi_model
        self.kimi_api_url = kimi_api_url
        self.http = httpx.Client(timeout=30.0)

        self.messages = DualMessagesProxy(self)

    def create(
        self,
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0,
        system: str = "",
        messages: List[Dict[str, str]] = [],
    ) -> DualAIResponse:
        providers = self._provider_order(model)
        errors: List[Exception] = []

        for provider in providers:
            try:
                if provider == "kimi":
                    text = self._generate_kimi(model, system, messages, temperature, max_tokens)
                else:
                    text = self._generate_claude(model, system, messages, temperature, max_tokens)

                if text:
                    return DualAIResponse(text)

                raise ValueError(f"{provider} returned an empty response")
            except Exception as exc:
                logger.warning("%s provider failed: %s", provider, exc)
                errors.append(exc)
                continue

        # If ANY provider hit a token/usage/quota limit, surface that even if a
        # later fallback provider failed for an unrelated reason (e.g. not
        # configured) and would otherwise mask the real cause.
        quota_error = next((exc for exc in errors if _is_quota_error(exc)), None)
        if quota_error:
            logger.error("AI usage limit/quota exceeded across all providers: %s", quota_error)
            raise AIQuotaExceededError(str(quota_error)) from quota_error

        last_error = errors[-1] if errors else None
        raise last_error or RuntimeError("DualAIClient failed to generate a response")

    def _provider_order(self, model: str) -> List[str]:
        normalized = model.lower() if model else ""
        if "claude" in normalized and self.anthropic_client:
            return ["claude", "kimi"]
        if "kimi" in normalized:
            if self.kimi_api_key:
                return ["kimi", "claude"]
            return ["claude"]
        if self.kimi_api_key:
            return ["kimi", "claude"]
        return ["claude"]

    def _generate_claude(
        self,
        model: str,
        system: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self.anthropic_client:
            raise ValueError("Anthropic API key is not configured")

        response = self.anthropic_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
        )

        return self._extract_claude_text(response)

    def _generate_kimi(
        self,
        model: str,
        system: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self.kimi_api_key:
            raise ValueError("Kimi API key is not configured")

        kimi_model = model if "kimi" in model.lower() else self.kimi_model
        payload = {
            "model": kimi_model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.kimi_api_key}"}

        try:
            response = self.http.post(self.kimi_api_url, json=payload, headers=headers)
            response.raise_for_status()
            return self._extract_kimi_text(response.json())
        except Exception as exc:
            raise self._wrap_kimi_error(exc)

    def _wrap_kimi_error(self, exc: Exception) -> Exception:
        message = str(exc) or repr(exc)
        lowered = message.lower()
        billing_terms = ("billing", "quota", "payment", "card", "insufficient funds", "forbidden", "402", "403")
        if any(term in lowered for term in billing_terms):
            logger.error("Kimi billing issue detected: %s", message)
            return RuntimeError(f"Kimi billing issue: {message}")
        return exc

    def _extract_kimi_text(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message", {})
            text = message.get("content")
            if isinstance(text, str):
                return text.strip()
            if isinstance(text, dict):
                parts = text.get("parts")
                if isinstance(parts, list) and parts:
                    return str(parts[0]).strip()

        if isinstance(data.get("text"), str):
            return data["text"].strip()

        raise ValueError("Kimi response missing text")

    def _extract_claude_text(self, response: Any) -> str:
        if response.content and len(response.content) > 0:
            return response.content[0].text.strip()
        raise ValueError("Claude response missing content")
