"""Framework-neutral composition root for the local Personal LMS runtime.

Wires the real ``OllamaProvider`` into the existing provider-neutral
contracts — registers it in a ``ProviderRegistry``, builds a
``DeterministicRouter`` over that registry, and constructs a local-only
``BudgetPolicy`` and a ``PersonalAssistantFlow``. Requires the optional
``ollama`` extra (``uv sync --extra ollama``): this module's whole purpose
is wiring up the real provider, so unlike ``personal_lms.config`` it is not
core-safe — importing it without ``httpx`` installed raises the existing
typed ``OllamaExtraNotInstalledError`` (see ``personal_lms.providers.ollama``),
the same pattern the CrewAI adapter uses for its own optional extra.

``compose()`` is a plain, synchronous function: it constructs objects and
never ``await``s anything, so it cannot perform any network I/O. No CLI,
Flow execution, or provider call happens here — this module only builds
the objects a caller (a future CLI vertical slice) will use.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from personal_lms.config import AppConfig
from personal_lms.domain.budgets import BudgetPolicy
from personal_lms.flows.personal_assistant import PersonalAssistantFlow
from personal_lms.policies.router import DeterministicRouter
from personal_lms.providers.ollama import OllamaProvider
from personal_lms.providers.registry import ProviderRegistry

# Local-only and every spending limit zeroed: DeterministicRouter treats
# local_only=True as a hard constraint (no hosted candidate is ever
# selected), and the zeroed limits are a second, independent layer — even
# a future caller-supplied routing preference that ignores local_only still
# hits daily_limit_usd == 0 and gets BudgetPolicyDeniedError for any hosted
# route. See DeterministicRouter.route() in policies/router.py.
_LOCAL_ONLY_BUDGET_POLICY_ID = "local_only_zero_hosted_spend"


def _build_local_only_budget_policy() -> BudgetPolicy:
    return BudgetPolicy(
        policy_id=_LOCAL_ONLY_BUDGET_POLICY_ID,
        daily_limit_usd=Decimal("0"),
        monthly_limit_usd=Decimal("0"),
        automatic_single_call_limit_usd=Decimal("0"),
        approval_single_call_limit_usd=Decimal("0"),
        local_only=True,
    )


@dataclass(frozen=True, slots=True)
class Application:
    """Composed runtime objects for the local Personal LMS vertical slice.

    Owns ``ollama_provider``'s HTTP client. Call ``aclose()`` exactly once
    when done — a future CLI's shutdown path, or a test's teardown.
    """

    config: AppConfig
    registry: ProviderRegistry
    router: DeterministicRouter
    budget_policy: BudgetPolicy
    flow: PersonalAssistantFlow
    ollama_provider: OllamaProvider

    async def aclose(self) -> None:
        """Close owned providers. Safe to call exactly once per ``Application``."""
        await self.ollama_provider.close()


def compose(config: AppConfig) -> Application:
    """Build one ``Application`` from ``config``.

    Synchronous and side-effect-free beyond in-memory object construction:
    ``OllamaProvider.__init__`` only opens an ``httpx.AsyncClient`` (which
    does not itself connect), and ``ProviderRegistry.register`` is a plain
    dict insert. No ``/api/version``, ``/api/tags``, ``/api/chat``, hosted
    API, or model-pull call happens here.
    """
    ollama_provider = OllamaProvider(config.ollama)

    registry = ProviderRegistry()
    registry.register(ollama_provider)

    router = DeterministicRouter(registry)
    budget_policy = _build_local_only_budget_policy()
    flow = PersonalAssistantFlow(router)

    return Application(
        config=config,
        registry=registry,
        router=router,
        budget_policy=budget_policy,
        flow=flow,
        ollama_provider=ollama_provider,
    )
