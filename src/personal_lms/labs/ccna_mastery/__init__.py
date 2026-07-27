"""CCNA mastery lab: wiring, gate contracts, and the nested CLI surface.

This package owns CCNA-specific wiring and gate entry points only. Every
generic capability it uses — pack loading and validation, evidence
eligibility, evidence review, extraction, retrieval — lives in its own
package and is injected here.

Nothing in this package starts the 48-hour validation clock, declares a
lab locked, invokes change control, approves a fixture, approves
evidence, or writes an approved golden artifact. Those are human acts,
and the code is arranged so no automated path can perform them.
"""

from __future__ import annotations

from personal_lms.labs.ccna_mastery.gates import (
    GateCheck,
    GateCheckStatus,
    GateId,
    GateReport,
    GateStatus,
    GoldenArtifactGuard,
    GoldenWriteRefusedError,
    ObservedGateReportStore,
)
from personal_lms.labs.ccna_mastery.wiring import (
    CcnaMasteryUseCase,
    EvidenceGateResult,
    EvidenceGateRunner,
    build_ccna_mastery_use_case,
    default_evidence_policy,
)

__all__ = [
    "CcnaMasteryUseCase",
    "EvidenceGateResult",
    "EvidenceGateRunner",
    "GateCheck",
    "GateCheckStatus",
    "GateId",
    "GateReport",
    "GateStatus",
    "GoldenArtifactGuard",
    "GoldenWriteRefusedError",
    "ObservedGateReportStore",
    "build_ccna_mastery_use_case",
    "default_evidence_policy",
]
