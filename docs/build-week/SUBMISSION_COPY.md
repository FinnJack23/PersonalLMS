# Grounded Tutor — An Evidence-Checked Personal LMS

Grounded Tutor converts a messy personal learning archive into approved
evidence, then produces cited micro-lessons, misconception checks, and mastery
records while clearly reporting retrieval gaps.

Learners need trustworthy explanations, but personal archives mix duplicates,
stale versions, unavailable placeholders, and unclear rights. Grounded Tutor
makes readiness visible before retrieval and limits teaching/drilling to
retrieved evidence. The flow is Source Readiness → approved inventory → fresh
retrieval → cited lesson → verification → three-question drill → SQLite mastery.

Deterministic Python services, existing provider-neutral routing, SQLite, and a
loopback-only interface form the local architecture. The OpenAI Responses
adapter supports configured GPT-5.6 use after privacy and approval checks;
offline mode is explicitly simulated. The cleaner contributes safe redacted
manifests, never raw files. Tracked fixtures contain synthetic identities only.
