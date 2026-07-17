"""Deterministic ``FakeSourceVerifier`` test double.

No network, filesystem, environment-variable, system-clock, or random
access — every returned result is exactly what the test configured, or a
configured typed failure. Never claims to be a real semantic verifier;
the model-backed Source Verifier is explicitly out of scope for this
milestone (see ``personal_lms.source_verification``'s package docstring).
"""

from __future__ import annotations

import asyncio

from personal_lms.domain.source_verification import (
    SourceVerificationRequest,
    SourceVerificationResult,
)
from personal_lms.source_verification.errors import SourceVerificationError


class FakeSourceVerifier:
    """Configurable, deterministic ``SourceVerifier`` test double.

    Configure exactly one of ``result``/``fail_with``. When ``result`` is
    configured, ``verify()`` always returns a ``SourceVerificationResult``
    whose ``request_id`` matches the *incoming* request — if the
    configured ``result.request_id`` differs, a corrected copy (every
    other configured field preserved) is returned instead. This fake
    never echoes a mismatched ``request_id``, matching what any
    well-behaved verifier (fake or real) must do (see
    ``personal_lms.source_verification.protocol.validate_result_matches_request``).
    """

    def __init__(
        self,
        verifier_id: str = "fake-source-verifier",
        *,
        result: SourceVerificationResult | None = None,
        fail_with: SourceVerificationError | None = None,
    ) -> None:
        if (result is None) == (fail_with is None):
            raise ValueError("FakeSourceVerifier requires exactly one of result= or fail_with=")

        self._verifier_id = verifier_id
        self._result = result
        self._fail_with = fail_with
        self.calls: list[SourceVerificationRequest] = []

    @property
    def verifier_id(self) -> str:
        return self._verifier_id

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def verify(self, request: SourceVerificationRequest) -> SourceVerificationResult:
        self.calls.append(request)
        await asyncio.sleep(0)

        if self._fail_with is not None:
            raise self._fail_with

        assert self._result is not None  # guaranteed by __init__'s exactly-one check
        if self._result.request_id == request.request_id:
            return self._result
        return self._result.model_copy(update={"request_id": request.request_id})
