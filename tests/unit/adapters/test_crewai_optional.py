from __future__ import annotations

import importlib.util
import subprocess
import sys

import pytest

CREWAI_INSTALLED = importlib.util.find_spec("crewai") is not None


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


def test_personal_lms_imports_successfully_without_crewai() -> None:
    result = _run_isolated("import personal_lms\nprint('OK')")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_domain_schemas_import_successfully_without_crewai() -> None:
    code = (
        "import personal_lms.domain\n"
        "import personal_lms.domain.agents\n"
        "import personal_lms.domain.models\n"
        "import personal_lms.domain.routing\n"
        "import personal_lms.domain.budgets\n"
        "print('OK')"
    )
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_providers_import_successfully_without_crewai() -> None:
    code = (
        "import personal_lms.providers\n"
        "import personal_lms.providers.registry\n"
        "import personal_lms.providers.fake\n"
        "print('OK')"
    )
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_policies_import_successfully_without_crewai() -> None:
    code = "import personal_lms.policies\nimport personal_lms.policies.router\nprint('OK')"
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_personal_assistant_flow_imports_and_runs_successfully_without_crewai() -> None:
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
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_no_core_import_pulls_in_crewai() -> None:
    code = """
import sys
import personal_lms
import personal_lms.domain
import personal_lms.domain.agents
import personal_lms.domain.models
import personal_lms.domain.routing
import personal_lms.providers
import personal_lms.providers.registry
import personal_lms.providers.fake
import personal_lms.policies
import personal_lms.policies.router
import personal_lms.flows
import personal_lms.flows.personal_assistant
assert "crewai" not in sys.modules, "a core import pulled in crewai"
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_adapter_package_import_alone_never_pulls_in_crewai() -> None:
    """import personal_lms.adapters.crewai (the bare package) must never
    import the external crewai package — only actually using
    CrewAIPersonalAssistantFlow may."""
    code = """
import sys
import personal_lms.adapters.crewai
assert "crewai" not in sys.modules
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_offline_defaults_are_applied_before_crewai_import_is_attempted() -> None:
    """Proves the bootstrap ordering directly: importing
    personal_assistant.py always applies the four offline defaults first,
    whether or not the subsequent crewai import succeeds — independent of
    any pytest fixture (this runs in a fresh subprocess with no conftest
    involved)."""
    code = """
import os
try:
    import personal_lms.adapters.crewai.personal_assistant
except ImportError:
    pass
assert os.environ.get("OTEL_SDK_DISABLED") == "true"
assert os.environ.get("CREWAI_DISABLE_VERSION_CHECK") == "true"
assert os.environ.get("CREWAI_TRACING_ENABLED") == "false"
assert os.environ.get("CREWAI_DISABLE_TRACKING") == "true"
print("OK")
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- Typed optional-dependency error ----------------------------------------


@pytest.mark.skipif(CREWAI_INSTALLED, reason="only meaningful when the crewai extra is absent")
def test_requesting_adapter_without_extra_raises_typed_error() -> None:
    import personal_lms.adapters.crewai as adapter_pkg
    from personal_lms.adapters.crewai import CrewAIExtraNotInstalledError

    with pytest.raises(CrewAIExtraNotInstalledError) as exc_info:
        _ = adapter_pkg.CrewAIPersonalAssistantFlow

    message = str(exc_info.value)
    assert "crewai" in message.lower()
    assert "extra" in message.lower()
    assert "install" in message.lower()


@pytest.mark.skipif(CREWAI_INSTALLED, reason="only meaningful when the crewai extra is absent")
def test_typed_error_is_raised_for_personal_assistant_flow_state_too() -> None:
    import personal_lms.adapters.crewai as adapter_pkg
    from personal_lms.adapters.crewai import CrewAIExtraNotInstalledError

    with pytest.raises(CrewAIExtraNotInstalledError):
        _ = adapter_pkg.PersonalAssistantFlowState


def test_typed_error_message_contains_no_secrets_or_dynamic_content() -> None:
    from personal_lms.adapters.crewai.runtime import CrewAIExtraNotInstalledError

    message = str(CrewAIExtraNotInstalledError())
    for forbidden in ("http://", "api_key", "password", "token=", "/home/", "secret"):
        assert forbidden not in message.lower()


@pytest.mark.skipif(not CREWAI_INSTALLED, reason="requires the crewai extra")
@pytest.mark.requires_crewai
def test_adapter_importable_when_extra_installed() -> None:
    from personal_lms.adapters.crewai import CrewAIPersonalAssistantFlow

    assert CrewAIPersonalAssistantFlow is not None
