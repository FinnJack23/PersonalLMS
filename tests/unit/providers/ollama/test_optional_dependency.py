from __future__ import annotations

import importlib.util
import subprocess
import sys

import pytest

HTTPX_INSTALLED = importlib.util.find_spec("httpx") is not None


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter, isolated from this test process's
    already-imported modules and any pytest fixture/conftest state."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )


# --- Core modules import cleanly, in isolation, regardless of mode ---------


def test_personal_lms_imports_successfully_without_httpx() -> None:
    result = _run_isolated("import personal_lms\nprint('OK')")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_no_core_import_pulls_in_httpx() -> None:
    code = """
import sys
import personal_lms
import personal_lms.domain
import personal_lms.providers
import personal_lms.providers.registry
import personal_lms.providers.fake
import personal_lms.policies
import personal_lms.policies.router
import personal_lms.flows
import personal_lms.flows.personal_assistant
assert "httpx" not in sys.modules, "a core import pulled in httpx"
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_framework_neutral_flow_runs_successfully_without_the_ollama_extra() -> None:
    code = """
import asyncio
from decimal import Decimal

from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.domain.enums import CostClass, LatencyClass
from personal_lms.domain.models import ModelCapabilityProfile, ModelRequest
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.flows.personal_assistant import PersonalAssistantFlow
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.fake import FakeLocalProvider
from personal_lms.providers.registry import ProviderRegistry

profile = ModelCapabilityProfile(
    profile_id="p",
    max_context_tokens=4096,
    is_local=True,
    max_privacy_classification=PrivacyClassification.RESTRICTED_LOCAL_ONLY,
    latency_class=LatencyClass.STANDARD,
    cost_class=CostClass.LOW,
)
registry = ProviderRegistry()
registry.register(FakeLocalProvider("solo", capability_profiles=(profile,)))
router = DeterministicRouter(registry)
flow = PersonalAssistantFlow(router)
request = ModelRequest(capability_profile="any", prompt="hello")
budget = BudgetPolicy(policy_id="d", daily_limit_usd=Decimal("3"), monthly_limit_usd=Decimal("40"))
result = asyncio.run(flow.run(request, budget_policy=budget))
assert result.provider_id == "solo"
import sys
assert "httpx" not in sys.modules
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_ollama_package_import_alone_never_pulls_in_httpx() -> None:
    code = """
import sys
import personal_lms.providers.ollama
assert "httpx" not in sys.modules
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_ollama_provider_config_is_usable_without_the_extra() -> None:
    """OllamaProviderConfig has no httpx dependency, so it must be
    constructible even in core-only mode."""
    code = """
from personal_lms.providers.ollama import OllamaProviderConfig
config = OllamaProviderConfig(provider_id="p", model="qwen2.5:7b", max_context_tokens=4096)
assert config.base_url == "http://127.0.0.1:11434"
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- Cross-extra independence ------------------------------------------------


def test_ollama_import_does_not_pull_in_crewai() -> None:
    code = """
import sys
import personal_lms.providers.ollama
assert "crewai" not in sys.modules
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_crewai_import_does_not_pull_in_the_ollama_provider_module() -> None:
    """CrewAI's own dependency tree (via the OpenAI SDK) legitimately uses
    httpx internally when the crewai extra is installed, so "httpx not in
    sys.modules" is not a meaningful independence check here. The actual
    claim this codebase makes is narrower: using the CrewAI adapter never
    reaches into our Ollama provider module."""
    code = """
import sys
try:
    import personal_lms.adapters.crewai.personal_assistant
except ImportError:
    pass
assert "personal_lms.providers.ollama" not in sys.modules
assert "personal_lms.providers.ollama.provider" not in sys.modules
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- Typed optional-dependency error ----------------------------------------


@pytest.mark.skipif(HTTPX_INSTALLED, reason="only meaningful when the ollama extra is absent")
def test_requesting_provider_without_extra_raises_typed_error() -> None:
    import personal_lms.providers.ollama as ollama_pkg
    from personal_lms.providers.ollama import OllamaExtraNotInstalledError

    with pytest.raises(OllamaExtraNotInstalledError) as exc_info:
        _ = ollama_pkg.OllamaProvider

    message = str(exc_info.value)
    assert "ollama" in message.lower()
    assert "extra" in message.lower()
    assert "install" in message.lower()


def test_typed_error_message_contains_no_secrets_or_dynamic_content() -> None:
    from personal_lms.providers.ollama.errors import OllamaExtraNotInstalledError

    message = str(OllamaExtraNotInstalledError())
    for forbidden in ("http://", "api_key", "password", "token=", "/home/", "secret"):
        assert forbidden not in message.lower()


@pytest.mark.skipif(not HTTPX_INSTALLED, reason="requires the ollama extra")
@pytest.mark.requires_ollama
def test_provider_importable_when_extra_installed() -> None:
    from personal_lms.providers.ollama import OllamaProvider

    assert OllamaProvider is not None
