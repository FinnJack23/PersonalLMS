"""Loopback-only judge interface using synthetic, approved evidence."""

# The single-page demo intentionally keeps its HTML template readable as one
# contiguous artifact; E501 is scoped to this presentation-only module.
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import html
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

from personal_lms.domain.citations import SourceCitation
from personal_lms.domain.librarian import GroundingBundle, RetrievedEvidence
from personal_lms.domain.privacy import PrivacyClassification
from personal_lms.mastery import MasteryRecord, SQLiteMasteryStore
from personal_lms.tutor.build_week import (
    DrillQuestion,
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


DEFAULT_DATABASE_PATH = Path(".local/personal-lms/grounded-tutor.sqlite3")


def _question_input(question: DrillQuestion) -> str:
    name = html.escape(question.question_id)
    if question.answer_choices:
        choices = "".join(
            f"<label><input type='radio' name='{name}' value='{html.escape(choice)}'> "
            f"{html.escape(choice)}</label><br>"
            for choice in question.answer_choices
        )
        return f"<fieldset><legend><b>{html.escape(question.question_text)}</b></legend>{choices}</fieldset>"
    return (
        f"<label><b>{html.escape(question.question_text)}</b><br>"
        f"<textarea name='{name}' rows='3' required></textarea></label>"
    )


def build_demo_page(
    result: TutorResponse, *, stored_record_count: int, error: str | None = None
) -> str:
    questions = "".join(
        f"<li>{_question_input(question)}</li>" for question in result.drill_questions
    )
    error_html = f"<p class='warn'>{html.escape(error)}</p>" if error else ""
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Grounded Tutor</title><style>body{{font:16px system-ui;max-width:980px;margin:2rem auto;padding:0 1rem;color:#172033}}section{{border:1px solid #ccd5e0;border-radius:10px;padding:1rem;margin:1rem 0}}fieldset{{margin:.75rem 0}}textarea{{width:100%}}.ok{{color:#176b3a}}.warn{{color:#8a4b08}}</style></head><body><h1>Grounded Tutor</h1><p>Offline demo mode — synthetic evidence only.</p><section><h2>1. Source Readiness</h2><p>Imported: 7 · Exact duplicate indicators: 2 · Placeholder excluded: 1 · Rights review: 1 · Approved: 2</p><button type='button'>Continue with approved evidence</button></section><section><h2>2. Learning Objective</h2><p><b>Explain connected and local Cisco routes.</b></p><p>Scope: networking · Privacy: internal-redacted · Maximum sources: 5</p></section><section><h2>3. Evidence Review</h2><p>Approved source: Synthetic networking route reference · Citation: E1 · Authority: approved demo evidence</p><blockquote>{html.escape(result.lesson)}</blockquote><p class='warn'>Retrieval gap: {html.escape(result.retrieval_gaps[0])}</p></section><section><h2>4. Lesson</h2><p class='ok'>Verification: {result.verification_status} · Model route: {result.model_route}</p><p>Inline citations are preserved as E1 markers from retrieved evidence.</p></section><section><h2>5. Drill and Mastery</h2>{error_html}<form method='post' action='/answers'><ol>{questions}</ol><button type='submit'>Submit all three answers</button></form><p>Stored review records: {stored_record_count}. Review results are stored in local SQLite; no Obsidian vault is required.</p></section></body></html>"""


def build_result_page(result: TutorResponse, records: tuple[MasteryRecord, ...]) -> str:
    questions = {question.question_id: question for question in result.drill_questions}
    feedback = "".join(
        f"<li class='{'ok' if record.correct else 'warn'}'><b>{html.escape(record.question_id)}: {'Correct' if record.correct else 'Incorrect'}</b><br>{html.escape(questions[record.question_id].explanation)} [{', '.join(questions[record.question_id].supporting_citation_ids)}]</li>"
        for record in records
    )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Grounded Tutor results</title><style>body{{font:16px system-ui;max-width:980px;margin:2rem auto;padding:0 1rem;color:#172033}}.ok{{color:#176b3a}}.warn{{color:#8a4b08}}</style></head><body><h1>Grounded Tutor results</h1><p>Deterministic evaluation completed locally.</p><ol>{feedback}</ol><p>Three review records were written to local SQLite.</p><p><a href='/'>Return to the lesson</a></p></body></html>"""


class DemoApplication:
    """Stateful offline fixture application with local-only mastery persistence."""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # HTTPServer handles one request at a time, but its serving thread can
        # differ from the thread that constructs this application (as in tests).
        self.mastery = SQLiteMasteryStore(
            sqlite3.connect(str(self.database_path), check_same_thread=False)
        )
        self.service = GroundedTutorBuildWeekService(DemoGrounding(), self.mastery)
        self.result = asyncio.run(
            self.service.teach(
                TutorRequest(
                    learning_objective="Explain connected and local Cisco routes.",
                    knowledge_scope="networking",
                    privacy_classification=PrivacyClassification.INTERNAL,
                    maximum_sources=5,
                )
            )
        )

    def page(self, *, error: str | None = None) -> str:
        return build_demo_page(
            self.result, stored_record_count=len(self.mastery.list()), error=error
        )

    def submit(self, form: dict[str, list[str]]) -> tuple[int, str]:
        questions = {question.question_id: question for question in self.result.drill_questions}
        if set(form) != set(questions) or any(
            len(form[question_id]) != 1 for question_id in questions
        ):
            return 400, self.page(
                error="Submit exactly one answer for each of the three questions."
            )
        answers = {question_id: values[0].strip() for question_id, values in form.items()}
        if any(not answer for answer in answers.values()):
            return 400, self.page(error="Each question requires a non-empty answer.")
        records = tuple(
            self.service.record_answer(self.result, questions[question_id], answers[question_id])
            for question_id in ("q1", "q2", "q3")
        )
        return 200, build_result_page(self.result, records)


def create_server(
    host: str = "127.0.0.1", port: int = 8000, database_path: str | Path = DEFAULT_DATABASE_PATH
) -> HTTPServer:
    application = DemoApplication(database_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/":
                self.send_error(404)
                return
            self._send(200, application.page())

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/answers":
                self.send_error(404)
                return
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else -1
            except ValueError:
                length = -1
            if length < 0 or length > 10_000:
                self._send(400, application.page(error="Malformed answer submission."))
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            status, page = application.submit(form)
            self._send(status, page)

        def _send(self, status: int, page: str) -> None:
            body = page.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return HTTPServer((host, port), Handler)


def serve(
    host: str = "127.0.0.1", port: int = 8000, database_path: str | Path = DEFAULT_DATABASE_PATH
) -> None:
    server = create_server(host, port, database_path)
    print(f"Grounded Tutor demo at http://{host}:{port} (offline simulated mode)")
    print(f"Local mastery database: {Path(database_path)}")
    server.serve_forever()
