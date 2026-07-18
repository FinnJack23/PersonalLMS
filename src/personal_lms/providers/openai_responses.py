"""Official OpenAI Responses API adapter for the Build Week demo.

The adapter is deliberately small and sits behind the existing provider
boundary. It sends only the redacted prompt supplied by the application.
"""

from __future__ import annotations

import os
import time
from typing import Any

from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest, ModelResult
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.providers.errors import ProviderError


class OpenAISetupError(ProviderError):
    """The live provider cannot run without safe local setup."""


class OpenAIResponsesProvider:
    provider_id = "openai-responses"
    is_local = False

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        *,
        store: bool = False,
        max_retries: int = 0,
    ) -> None:
        model_name = model or os.getenv("PERSONAL_LMS_BUILD_WEEK_MODEL") or "gpt-5.6"
        self.model: str = model_name
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if store:
            raise ValueError("Grounded Tutor live validation requires store=False")
        if max_retries != 0:
            raise ValueError("Grounded Tutor live validation disables retries")
        self.store = False
        self.max_retries = 0
        self.capability_profiles = (
            ModelCapabilityProfile(
                profile_id=self.model,
                supports_reasoning=True,
                supports_structured_output=True,
                max_context_tokens=128000,
                is_local=False,
                max_privacy_classification=PrivacyClassification.PUBLIC,
                latency_class=LatencyClass.STANDARD,
                cost_class=CostClass.MEDIUM,
            ),
        )

    async def generate(self, request: ModelRequest) -> ModelResult:
        if not self._api_key:
            raise OpenAISetupError("OPENAI_API_KEY is required for live GPT-5.6 mode")
        if request.privacy_classification is not PrivacyClassification.PUBLIC:
            raise OpenAISetupError("only public content may use hosted routing")
        try:
            import httpx
        except ImportError as exc:
            raise OpenAISetupError("install the optional httpx runtime to use live mode") from exc
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self.model,
            "input": request.prompt,
            "store": False,
            "text": {"format": {"type": "text"}},
        }
        if request.max_output_tokens is not None:
            payload["max_output_tokens"] = request.max_output_tokens
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise OpenAISetupError("OpenAI Responses request failed") from exc
        output = data.get("output_text")
        if not isinstance(output, str):
            text_parts: list[str] = []
            for item in data.get("output", []):
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        text = content.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
            output = "\n".join(text_parts)
        if not isinstance(output, str) or not output.strip():
            raise OpenAISetupError("OpenAI Responses returned no text output")
        usage = data.get("usage") or {}
        return ModelResult(
            request_id=request.request_id,
            capability_profile=self.model,
            is_local=False,
            output_text=output,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason=str(data.get("status") or "stop"),
        )
