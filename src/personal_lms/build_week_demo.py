"""Loopback-only judge interface using synthetic, approved evidence."""

# The single-page demo intentionally keeps its HTML template readable as one
# contiguous artifact; E501 is scoped to this presentation-only module.
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import html
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from uuid import uuid4

from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.librarian import GroundingBundle, RetrievedEvidence
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.mastery import SQLiteMasteryStore
from personal_lms.tutor.build_week import (
    GroundedTutorBuildWeekService,
    TutorRequest,
    TutorResponse,
)


class DemoGrounding:
    def retrieve(self, objective: str, scope: str, maximum_sources: int) -> GroundingBundle:
        evidence = [
            RetrievedEvidence(
                citation=SourceCitation(
                    source_id="demo-networking-approved",
                    title="Synthetic networking route reference",
                    location="section 2",
                    approved=True,
                ),
                text="A connected route represents the network assigned to an active router interface. A local route represents the router's exact interface address. The local IPv4 route uses a host prefix such as /32. Both may appear after an interface is configured and operational.",
                trusted_for_rag=True,
            ),
        ][:maximum_sources]
        return GroundingBundle(
            request_id=uuid4(),
            evidence=evidence,
            is_sufficient=True,
            gaps=["The retrieved demo evidence does not establish route administrative distance."],
        )


def build_demo_page(result: TutorResponse) -> str:
    questions = "".join(
        f"<li><b>{html.escape(q.question_text)}</b><br>Answer: {html.escape(q.correct_answer)}<br><small>{html.escape(q.explanation)} [{', '.join(q.supporting_citation_ids)}]</small></li>"
        for q in result.drill_questions
    )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Grounded Tutor</title><style>body{{font:16px system-ui;max-width:980px;margin:2rem auto;padding:0 1rem;color:#172033}}section{{border:1px solid #ccd5e0;border-radius:10px;padding:1rem;margin:1rem 0}}.ok{{color:#176b3a}}.warn{{color:#8a4b08}}</style></head><body><h1>Grounded Tutor</h1><p>Offline demo mode — synthetic evidence only.</p><section><h2>1. Source Readiness</h2><p>Imported: 7 · Exact duplicate indicators: 2 · Placeholder excluded: 1 · Rights review: 1 · Approved: 2</p><button>Continue with approved evidence</button></section><section><h2>2. Learning Objective</h2><p><b>Explain connected and local Cisco routes.</b></p><p>Scope: networking · Privacy: internal-redacted · Maximum sources: 5</p></section><section><h2>3. Evidence Review</h2><p>Approved source: Synthetic networking route reference · Citation: E1 · Authority: approved demo evidence</p><blockquote>{html.escape(result.lesson)}</blockquote><p class='warn'>Retrieval gap: {html.escape(result.retrieval_gaps[0])}</p></section><section><h2>4. Lesson</h2><p class='ok'>Verification: {result.verification_status} · Model route: {result.model_route}</p><p>Inline citations are preserved as E1 markers from retrieved evidence.</p></section><section><h2>5. Drill and Mastery</h2><ol>{questions}</ol><p>Review results are stored in local SQLite; no Obsidian vault is required.</p></section></body></html>"""


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    mastery = SQLiteMasteryStore(sqlite3.connect(":memory:"))
    result = asyncio.run(
        GroundedTutorBuildWeekService(DemoGrounding(), mastery).teach(
            TutorRequest(
                learning_objective="Explain connected and local Cisco routes.",
                knowledge_scope="networking",
                privacy_classification=PrivacyClassification.INTERNAL,
                maximum_sources=5,
            )
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = build_demo_page(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    print(f"Grounded Tutor demo at http://{host}:{port} (offline simulated mode)")
    HTTPServer((host, port), Handler).serve_forever()
