# objective-1.5-shadow — intentionally not authored in P0

Per `docs/plans/ccna-mastery-micro-lab/IMPLEMENTATION_PLAN.md` (WP10) and
`LINCHPIN_TRACEABILITY.md` (G3-SH-01..03, GLOBAL-09), the objective-1.5
("Compare TCP to UDP") shadow diagnostic pack is Gate 3 factory-scalability
content, not P0 pre-clock content. The base manifest and P0 freeze cover only
Gate 1, Gate 2, and the approved route fixture.

Authoring this pack now would violate the explicit constraint in
`IMPLEMENTATION_PLAN.md` WP10 and `11-48-HOUR-LINCHPIN-GATES.md` Gate 3:
the factory-supplement clock "starts only after shadow-objective evidence is
ready," and G3-FS-GO-02 / G3-FS-NG-02 measure the *first-pass authoring time*
of this pack after evidence readiness — a pre-authored pack would falsify
that measurement.

This directory is a placeholder so the required `tests/linchpin/` tree exists
now. It intentionally contains no pack content. When Gate 3 begins, a
`factory-supplement.yaml` (freezing criteria, evidence-ready inputs, start
time, and final authored-pack hash — not the completed pack itself) is added
first, per `IMPLEMENTATION_PLAN.md` WP10.
