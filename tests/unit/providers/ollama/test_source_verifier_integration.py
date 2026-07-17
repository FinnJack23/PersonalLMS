"""Integration coverage: ModelBackedSourceVerifier -> DeterministicRouter
-> OllamaProvider (with an injected fake chat client) -> SourceVerificationResult.

Proves the router selects the local Ollama provider, the provider is
called exactly once, strict JSON output from it reaches
SourceVerificationResult, and request correlation is deterministic — all
without a live Ollama service and with no Tutor code changes required.

Still requires httpx to be installed (see test_chat_client_injection.py's
module docstring for why): the same skip guard applies here.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

if importlib.util.find_spec("httpx") is None:
    pytest.skip(
        "httpx (ollama extra) not installed (uv sync --extra ollama)", allow_module_level=True
    )

from personal_lms.domain import (
    BudgetPolicy,
    GroundingBundle,
    PrivacyClassification,
    RetrievedEvidence,
    SourceCitation,
    SourceVerificationRequest,
    SourceVerificationStatus,
)
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.ollama.provider import OllamaProvider
from personal_lms.providers.registry import ProviderRegistry
from personal_lms.source_verification import ModelBackedSourceVerifier

from .conftest import make_config

pytestmark = pytest.mark.requires_ollama


class _FakeChatClient:
    """Pure-Python OllamaChatClient returning a fixed verification JSON body."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
        options: dict[str, Any],
        keep_alive: str | None,
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "messages": messages, "stream": stream})
        return {
            "message": {"role": "assistant", "content": self._content},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 10,
            "eval_count": 5,
            "total_duration": 1_000_000,
        }


def _bundle() -> GroundingBundle:
    return GroundingBundle(
        request_id=uuid4(),
        evidence=[
            RetrievedEvidence(
                citation=SourceCitation(source_id="src-1", title="Routing Concepts"),
                text="The OSPF DR election is decided by priority, then router ID.",
                trusted_for_rag=True,
            )
        ],
        is_sufficient=True,
    )


def test_ollama_local_provider_serves_a_verified_source_verification_result() -> None:
    request = SourceVerificationRequest(
        request_id=str(uuid4()),
        generated_text="Priority wins. [E1]",
        grounding_bundle=_bundle(),
        used_citation_labels=("E1",),
        privacy_classification=PrivacyClassification.INTERNAL,
    )
    verification_json = json.dumps(
        {
            "request_id": request.request_id,
            "status": "verified",
            "claims": [
                {
                    "claim_id": "C1",
                    "status": "supported",
                    "evidence_labels": ["E1"],
                    "reason_codes": [],
                }
            ],
            "verified_citation_labels": ["E1"],
            "unsupported_claim_count": 0,
            "conflict_count": 0,
            "semantic_confidence": 0.9,
            "reason_codes": [],
        }
    )
    fake_chat = _FakeChatClient(verification_json)
    provider = OllamaProvider(
        make_config(provider_id="ollama-local", model="qwen2.5:7b"), chat_client=fake_chat
    )

    registry = ProviderRegistry()
    registry.register(provider)
    router = DeterministicRouter(registry)
    budget_policy = BudgetPolicy(
        policy_id="p", daily_limit_usd=Decimal("3.00"), monthly_limit_usd=Decimal("40.00")
    )
    verifier = ModelBackedSourceVerifier(
        verifier_id="ollama-local-verifier", router=router, budget_policy=budget_policy
    )

    result = asyncio.run(verifier.verify(request))

    # The router selected the (only) local provider.
    assert len(fake_chat.calls) == 1
    assert result.status is SourceVerificationStatus.VERIFIED
    assert result.request_id == request.request_id
    assert result.semantic_confidence == 0.9


def test_ollama_local_provider_is_called_exactly_once_for_verification() -> None:
    request = SourceVerificationRequest(
        request_id=str(uuid4()),
        generated_text="Priority wins. [E1]",
        grounding_bundle=_bundle(),
        used_citation_labels=("E1",),
        privacy_classification=PrivacyClassification.INTERNAL,
    )
    verification_json = json.dumps(
        {
            "request_id": request.request_id,
            "status": "rejected",
            "claims": [
                {
                    "claim_id": "C1",
                    "status": "unsupported",
                    "evidence_labels": ["E1"],
                    "reason_codes": [],
                }
            ],
            "verified_citation_labels": [],
            "unsupported_claim_count": 1,
            "conflict_count": 0,
            "semantic_confidence": None,
            "reason_codes": ["unsupported_claim"],
        }
    )
    fake_chat = _FakeChatClient(verification_json)
    provider = OllamaProvider(make_config(), chat_client=fake_chat)

    registry = ProviderRegistry()
    registry.register(provider)
    router = DeterministicRouter(registry)
    budget_policy = BudgetPolicy(
        policy_id="p", daily_limit_usd=Decimal("3.00"), monthly_limit_usd=Decimal("40.00")
    )
    verifier = ModelBackedSourceVerifier(
        verifier_id="ollama-local-verifier", router=router, budget_policy=budget_policy
    )

    result = asyncio.run(verifier.verify(request))

    assert len(fake_chat.calls) == 1
    assert result.status is SourceVerificationStatus.REJECTED
