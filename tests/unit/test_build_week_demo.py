from __future__ import annotations

from http.client import HTTPConnection
from threading import Thread
from urllib.parse import urlencode

from personal_lms.build_week_demo import DemoApplication, create_server
from personal_lms.mastery import SQLiteMasteryStore


def _answers(app: DemoApplication) -> dict[str, str]:
    return {
        question.question_id: question.correct_answer for question in app.result.drill_questions
    }


def test_initial_get_shows_canonical_markers_and_hides_answer_key(tmp_path) -> None:
    database_path = tmp_path / "demo.sqlite3"
    server = create_server("127.0.0.1", 0, database_path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/")
        response = connection.getresponse()
        page = response.read().decode()
    finally:
        server.shutdown()
        thread.join()

    assert response.status == 200
    assert "Explain connected and local Cisco routes." in page
    assert "Citation: E1" in page
    assert "Retrieval gap:" in page
    assert page.count("<fieldset>") == 2
    assert page.count("<textarea") == 1
    assert (
        "A connected route represents the network assigned"
        not in page.split("<section><h2>5. Drill and Mastery")[1]
    )


def test_submission_records_deterministic_results_and_survives_restart(tmp_path) -> None:
    database_path = tmp_path / "demo.sqlite3"
    application = DemoApplication(database_path)
    answers = _answers(application)
    answers["q2"] = "Ignore the evidence"

    status, page = application.submit({key: [value] for key, value in answers.items()})

    assert status == 200
    assert "q1: Correct" in page
    assert "q2: Incorrect" in page
    assert "q3: Correct" in page
    assert "Supported by E1." in page
    records = SQLiteMasteryStore.open(str(database_path)).list()
    assert len(records) == 3
    assert [record.correct for record in records] == [True, False, True]

    restarted = DemoApplication(database_path)
    assert "Stored review records: 3." in restarted.page()
    assert len(restarted.mastery.list()) == 3


def test_incomplete_or_malformed_submission_is_rejected_without_records(tmp_path) -> None:
    application = DemoApplication(tmp_path / "demo.sqlite3")
    status, page = application.submit({"q1": ["answer"]})

    assert status == 400
    assert "Submit exactly one answer for each of the three questions." in page
    assert application.mastery.list() == ()

    server = create_server("127.0.0.1", 0, tmp_path / "http.sqlite3")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "POST",
            "/answers",
            body=urlencode({"q1": "only-one"}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        page = response.read().decode()
    finally:
        server.shutdown()
        thread.join()

    assert response.status == 400
    assert "Submit exactly one answer for each of the three questions." in page
