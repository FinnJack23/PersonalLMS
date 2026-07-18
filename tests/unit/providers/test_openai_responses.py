import asyncio
import sys
from types import SimpleNamespace

import pytest

from personal_lms.domain.models import ModelRequest
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.providers.openai_responses import OpenAIResponsesProvider, OpenAISetupError


class _Response:
    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, object]:
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "supported answer"}],
                }
            ],
            "usage": {"input_tokens": 4, "output_tokens": 3},
            "status": "completed",
        }


class _Client:
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response()


def _request(privacy: PrivacyClassification = PrivacyClassification.PUBLIC) -> ModelRequest:
    return ModelRequest(
        capability_profile="gpt-5.6",
        prompt="synthetic public evidence",
        privacy_classification=privacy,
        max_output_tokens=32,
    )


def test_responses_payload_is_nonpersistent_and_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.calls = []
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_Client))
    result = asyncio.run(OpenAIResponsesProvider(api_key="synthetic-test-key").generate(_request()))
    assert result.output_text == "supported answer"
    assert _Client.calls[0]["json"]["store"] is False  # type: ignore[index]
    assert OpenAIResponsesProvider(api_key="synthetic-test-key").max_retries == 0


@pytest.mark.parametrize(
    "privacy",
    [
        PrivacyClassification.INTERNAL,
        PrivacyClassification.SENSITIVE,
        PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    ],
)
def test_non_public_content_is_rejected_before_transport(
    monkeypatch: pytest.MonkeyPatch, privacy: PrivacyClassification
) -> None:
    class _ForbiddenClient:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("transport must not be constructed")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_ForbiddenClient))
    with pytest.raises(OpenAISetupError):
        asyncio.run(
            OpenAIResponsesProvider(api_key="synthetic-test-key").generate(_request(privacy))
        )


def test_non_default_store_or_retries_are_rejected() -> None:
    with pytest.raises(ValueError):
        OpenAIResponsesProvider(api_key="synthetic-test-key", store=True)
    with pytest.raises(ValueError):
        OpenAIResponsesProvider(api_key="synthetic-test-key", max_retries=1)
