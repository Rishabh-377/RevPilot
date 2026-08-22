"""
LLM Client Interface & Gemini Provider
=======================================

Provides a robust HTTP client for Google's Gemini REST API with:
- Configurable model (default: gemini-2.5-flash)
- Environment-based credential resolution (GEMINI_API_KEY, GOOGLE_API_KEY, REVPILOT_GEMINI_API_KEY)
- Strict request timeout handling
- Exponential backoff retry policy for transient errors (429, 500, 502, 503, 504, network timeouts)
- Fast-fail on non-retryable client/auth errors (400, 401, 403)
- Separation of concerns: only performs text generation; validation happens in callers.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class LLMClientProtocol(Protocol):
    """Protocol defining the required interface for LLM diagnosis clients."""

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        ...

    async def generate_async(self, prompt: str, system_prompt: str | None = None) -> str:
        ...


class GeminiClient:
    """Production REST client for Google Gemini models via generativelanguage.googleapis.com."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        timeout_seconds: float = 5.0,
        max_retries: int = 2,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("REVPILOT_GEMINI_API_KEY")
        )
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.base_url = base_url.rstrip("/")

    @property
    def is_configured(self) -> bool:
        """Check if an API key is present."""
        return bool(self.api_key and self.api_key.strip())

    def _build_payload(self, prompt: str, system_prompt: str | None = None) -> dict[str, Any]:
        """Construct Gemini generateContent request payload."""
        payload: dict[str, Any] = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        }
        if system_prompt:
            payload["system_instruction"] = {
                "parts": [{"text": system_prompt}]
            }
        return payload

    def _build_url(self) -> str:
        """Construct API endpoint URL."""
        if not self.is_configured:
            raise ValueError("Gemini API key is not configured. Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable.")
        return f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"

    def _extract_text_from_response(self, data: dict[str, Any]) -> str:
        """Extract generated text from Gemini response JSON structure."""
        candidates = data.get("candidates", [])
        if not candidates:
            feedback = data.get("promptFeedback", {})
            raise ValueError(f"Gemini returned no candidates. Prompt feedback: {feedback}")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts or "text" not in parts[0]:
            raise ValueError(f"Gemini candidate contained no text parts: {candidates[0]}")

        return str(parts[0]["text"]).strip()

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Synchronous text generation with retries on transient errors."""
        url = self._build_url()
        payload = self._build_payload(prompt, system_prompt)

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.post(url, json=payload, headers={"Content-Type": "application/json"})

                if resp.status_code == 200:
                    return self._extract_text_from_response(resp.json())

                # Fast fail on non-retryable client errors (auth, bad request)
                if resp.status_code in (400, 401, 403, 404):
                    resp.raise_for_status()

                # Retryable status codes: 429, 500, 502, 503, 504
                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        f"Gemini API returned retryable status {resp.status_code} (attempt {attempt + 1}/{self.max_retries + 1})"
                    )
                    resp.raise_for_status()

                resp.raise_for_status()

            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
                last_err = e
                # Check if non-retryable
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (400, 401, 403, 404):
                    raise

                if attempt < self.max_retries:
                    backoff = 0.5 * (2 ** attempt)
                    logger.info(f"Retrying Gemini API call after {backoff:.2f}s due to: {e}")
                    time.sleep(backoff)
                else:
                    logger.error(f"Gemini API call failed after {self.max_retries + 1} attempts: {e}")
                    raise

        if last_err:
            raise last_err
        raise RuntimeError("Gemini generate completed without response or exception")

    async def generate_async(self, prompt: str, system_prompt: str | None = None) -> str:
        """Asynchronous text generation with retries on transient errors."""
        import asyncio

        url = self._build_url()
        payload = self._build_payload(prompt, system_prompt)

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})

                if resp.status_code == 200:
                    return self._extract_text_from_response(resp.json())

                if resp.status_code in (400, 401, 403, 404):
                    resp.raise_for_status()

                if resp.status_code in (429, 500, 502, 503, 504):
                    logger.warning(
                        f"Gemini API returned retryable status {resp.status_code} (attempt {attempt + 1}/{self.max_retries + 1})"
                    )
                    resp.raise_for_status()

                resp.raise_for_status()

            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
                last_err = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (400, 401, 403, 404):
                    raise

                if attempt < self.max_retries:
                    backoff = 0.5 * (2 ** attempt)
                    await asyncio.sleep(backoff)
                else:
                    logger.error(f"Gemini API async call failed after {self.max_retries + 1} attempts: {e}")
                    raise

        if last_err:
            raise last_err
        raise RuntimeError("Gemini generate_async completed without response or exception")
