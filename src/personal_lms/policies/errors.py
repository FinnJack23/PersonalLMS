from __future__ import annotations

from personal_lms.domain.routing import RoutingDecision
from personal_lms.providers.errors import ProviderError


class RoutingError(ProviderError):
    """Base class for router-level failures.

    Every instance carries the ``RoutingDecision`` the router would have
    recorded for audit purposes — outcome ``rejected`` plus machine-readable
    reasons — so callers can log or inspect *why* routing failed without the
    exception message alone having to carry that detail. Never carries
    prompt text or secrets, matching ``ProviderError``.
    """

    def __init__(self, message: str, *, decision: RoutingDecision) -> None:
        super().__init__(message)
        self.decision = decision


class NoCompatibleProviderError(RoutingError):
    """Raised when no registered provider satisfies the routing criteria.

    Covers both an empty registry (reason ``provider_registry_empty``) and
    a populated registry where nothing qualifies — an empty registry is not
    a malformed request, just an environment with nothing configured yet,
    so it does not warrant its own error type.
    """

    def __init__(self, decision: RoutingDecision) -> None:
        super().__init__(
            "No registered provider satisfies the requested routing criteria",
            decision=decision,
        )


class PrivacyPolicyDeniedError(RoutingError):
    """Raised when privacy classification is specifically what blocked routing.

    Fires only when a hosted candidate would otherwise have qualified —
    i.e. privacy policy, not capability mismatch, is the reason routing
    failed.
    """

    def __init__(self, decision: RoutingDecision) -> None:
        super().__init__(
            "Privacy classification prevents routing to any hosted provider",
            decision=decision,
        )


class LocalProviderRequiredError(RoutingError):
    """Raised when local-only routing is required but no local provider qualifies."""

    def __init__(self, decision: RoutingDecision) -> None:
        super().__init__(
            "Local-only routing is required but no local provider is available",
            decision=decision,
        )


class BudgetPolicyDeniedError(RoutingError):
    """Raised when budget policy blocks the only remaining hosted candidates."""

    def __init__(self, decision: RoutingDecision) -> None:
        super().__init__(
            "Budget policy blocks hosted routing for this request",
            decision=decision,
        )
