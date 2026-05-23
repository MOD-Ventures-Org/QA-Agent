import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import anthropic
import httpx
from anthropic import Anthropic

logger = logging.getLogger(__name__)


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
        gemini_api_key: str = "",
        gemini_model: str = "gemini-1.5-turbo",
    ):
        self.anthropic_client: Optional[Anthropic] = None
        if anthropic_api_key:
            self.anthropic_client = Anthropic(api_key=anthropic_api_key)

        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model
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
        last_error = None

        for provider in providers:
            try:
                if provider == "gemini":
                    text = self._generate_gemini(model, system, messages, temperature, max_tokens)
                else:
                    text = self._generate_claude(model, system, messages, temperature, max_tokens)

                if text:
                    return DualAIResponse(text)

                raise ValueError(f"{provider} returned an empty response")
            except Exception as exc:
                logger.warning("%s provider failed: %s", provider, exc)
                last_error = exc
                continue

        raise last_error or RuntimeError("DualAIClient failed to generate a response")

    def _provider_order(self, model: str) -> List[str]:
        normalized = model.lower() if model else ""
        if "claude" in normalized and self.claude_client:
            return ["claude", "gemini"]
        if "gemini" in normalized and self.gemini_api_key:
            return ["gemini", "claude"]
        if self.gemini_api_key:
            return ["gemini", "claude"]
        return ["claude", "gemini"]

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

    def _generate_gemini(
        self,
        model: str,
        system: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self.gemini_api_key:
            raise ValueError("Gemini API key is not configured")

        gemini_model = model if "gemini" in model.lower() else self.gemini_model
        url = f"https://gemini.googleapis.com/v1/models/{gemini_model}:generateMessage?key={self.gemini_api_key}"
        payload = {
            "temperature": temperature,
            "candidateCount": 1,
            "maxOutputTokens": max_tokens,
            "messages": [
                {"author": "system", "content": {"type": "text", "text": system}}
            ]
            + [
                {"author": msg["role"], "content": {"type": "text", "text": msg["content"]}}
                for msg in messages
            ],
        }

        response = self.http.post(url, json=payload)
        response.raise_for_status()
        return self._extract_gemini_text(response.json())

    def _extract_claude_text(self, response: Any) -> str:
        if response.content and len(response.content) > 0:
            return response.content[0].text.strip()
        raise ValueError("Claude response missing content")

    def _extract_gemini_text(self, data: Dict[str, Any]) -> str:
        for candidate in data.get("candidates", []):
            for content in candidate.get("content", []):
                if isinstance(content, dict):
                    text = content.get("text")
                    if text:
                        return text.strip()
        raise ValueError("Gemini response missing text")
