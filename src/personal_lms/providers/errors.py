from __future__ import annotations


class ProviderError(Exception):
    """Base class for all provider-layer errors.

    Messages carry a provider ID and a short reason only — never a prompt,
    document content, or credential. Callers must not pass request payloads
    into these constructors.
    """


class ProviderNotFoundError(ProviderError):
    def __init__(self, provider_id: str) -> None:
        super().__init__(f"No provider registered with id {provider_id!r}")
        self.provider_id = provider_id


class ProviderAlreadyRegisteredError(ProviderError):
    def __init__(self, provider_id: str) -> None:
        super().__init__(f"A provider is already registered with id {provider_id!r}")
        self.provider_id = provider_id


class ProviderUnavailableError(ProviderError):
    def __init__(self, provider_id: str, reason: str = "") -> None:
        message = f"Provider {provider_id!r} is unavailable"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.provider_id = provider_id


class ProviderTimeoutError(ProviderError):
    def __init__(self, provider_id: str, timeout_seconds: float) -> None:
        super().__init__(f"Provider {provider_id!r} timed out after {timeout_seconds}s")
        self.provider_id = provider_id
        self.timeout_seconds = timeout_seconds


class ProviderExecutionError(ProviderError):
    def __init__(self, provider_id: str, reason: str = "") -> None:
        message = f"Provider {provider_id!r} failed to execute the request"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.provider_id = provider_id


class ProviderContractError(ProviderError):
    def __init__(self, provider_id: str, reason: str = "") -> None:
        message = f"Provider {provider_id!r} violated its contract"
        if reason:
            message = f"{message}: {reason}"
        super().__init__(message)
        self.provider_id = provider_id
