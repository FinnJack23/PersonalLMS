"""Bounded lab feature adapters.

A lab package owns only its own wiring, fixture and gate entry points, and
any domain-specific adapter it genuinely needs. It never owns generic
source, retrieval, Tutor, routing, provider, privacy, budget, event,
mastery, or scheduling implementations — those live in their own packages
and are supplied to a lab by root composition.

The rule that keeps this honest: if a lab package would need to construct
a router, a provider registry, a content repository, or a Tutor, that is
a signal the dependency should have been injected instead.
"""

from __future__ import annotations

__all__: list[str] = []
