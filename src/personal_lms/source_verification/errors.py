"""Typed Source Verifier failures.

Source-verification failure is a *service-layer* failure, not necessarily
a model-provider failure — these deliberately do not subclass
``personal_lms.providers.errors.ProviderError``, mirroring
``personal_lms.policies.errors.RoutingError``'s own separate hierarchy for
policy-layer failures elsewhere in this codebase. Every message carries
safe, machine-readable context only (a verifier identifier and a short
reason) — never generated response text, evidence text, the original
prompt, credentials, or transport/provider details. Callers must not pass
request payloads into these constructors.
"""

from __future__ import annotations


class SourceVerificationError(Exception):
    """Base class for all Source Verifier failures."""


class SourceVerificationUnavailableError(SourceVerificationError):
    """The configured verifier could not be reached or is not ready."""

    def __init__(self, verifier_id: str, reason: str = "") -> None:
        message = f"Source verifier {verifier_id!r} is unavailable"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.verifier_id = verifier_id


class SourceVerificationExecutionError(SourceVerificationError):
    """The configured verifier failed while executing the request."""

    def __init__(self, verifier_id: str, reason: str = "") -> None:
        message = f"Source verifier {verifier_id!r} failed to execute the request"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.verifier_id = verifier_id


class SourceVerificationContractError(SourceVerificationError):
    """The verifier violated its structural contract (e.g. an unknown
    evidence label, or a result whose request_id does not match)."""

    def __init__(self, verifier_id: str, reason: str = "") -> None:
        message = f"Source verifier {verifier_id!r} violated its contract"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.verifier_id = verifier_id


class SourceVerificationPrivacyError(SourceVerificationError):
    """The verifier could not honor the request's privacy classification."""

    def __init__(self, verifier_id: str, reason: str = "") -> None:
        message = f"Source verifier {verifier_id!r} violated privacy policy"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.verifier_id = verifier_id
