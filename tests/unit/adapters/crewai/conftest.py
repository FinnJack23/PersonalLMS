"""Guarantee this test package is offline and filesystem-clean.

Set as real ``os.environ`` entries at module import time — deliberately
*not* via ``monkeypatch.setenv``, which reverts at each test's teardown.
CrewAI registers an ``atexit`` handler (``crewai_event_bus.shutdown``) that
runs after pytest has finished and every per-test fixture has already been
torn down; a monkeypatch-based approach leaves the environment unset again
by the time that handler fires, undermining the hermeticity these settings
exist to guarantee. These stay set for the rest of the process, which is
the whole point.

``CREWAI_TESTING`` is CrewAI's own documented signal for "skip the
first-execution tracing-consent prompt and file write" — separate from
``CrewAIPersonalAssistantFlow``'s own ``setdefault``-based production
defaults (which cannot retroactively un-write a consent file already
created by an earlier, unrelated CrewAI invocation on this machine).
"""

import os

os.environ["CREWAI_TESTING"] = "true"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_DISABLE_VERSION_CHECK"] = "true"
os.environ["CREWAI_TRACING_ENABLED"] = "false"
os.environ["CREWAI_DISABLE_TRACKING"] = "true"
