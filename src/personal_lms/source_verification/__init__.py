"""Source Verifier: a domain-neutral, provider-neutral semantic
claim-support verification contract.

Answers a strictly different question than structural citation-label
validation (see ``personal_lms.tutor._generation.verify_citations``): do
the cited evidence passages actually *support* the generated claims?

This milestone establishes only the contract, typed failures, and a
deterministic fake — no real LLM-backed, Ollama-backed, or hosted-backed
verifier is implemented here. A future model-backed Source Verifier is a
separate, later milestone.
"""

from personal_lms.source_verification.errors import (
    SourceVerificationContractError,
    SourceVerificationError,
    SourceVerificationExecutionError,
    SourceVerificationPrivacyError,
    SourceVerificationUnavailableError,
)
from personal_lms.source_verification.fake import FakeSourceVerifier
from personal_lms.source_verification.protocol import (
    SourceVerifier,
    validate_result_matches_request,
)

__all__ = [
    "FakeSourceVerifier",
    "SourceVerificationContractError",
    "SourceVerificationError",
    "SourceVerificationExecutionError",
    "SourceVerificationPrivacyError",
    "SourceVerificationUnavailableError",
    "SourceVerifier",
    "validate_result_matches_request",
]
