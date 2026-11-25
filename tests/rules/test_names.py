import json
import re
from pathlib import Path

import httpx
import pytest

import chalkboard
from chalkboard import ai, jobs
from chalkboard.ai import FACTORY_KEY, TRANSPORT_KEY, RedactingClient, model_client
from chalkboard.ai.base import EMBED_DIMENSIONS, OllamaConfig
from chalkboard.ai.ollama import OllamaClient
from chalkboard.models import Student, Subject
from tests.conftest import csrf_token, sign_in
from tests.test_chapters_page import approved_with_lessons
from tests.test_today_page import sourced, timetable

STUDENTS = ["Leo Martins", "Yusuf Okafor", "Mia Chen"]
SOURCE = Path(chalkboard.__file__).parent
# Reuse the Today page fixtures; `sourced` builds on this module's `subject`, which has the students.
fixtures = (sourced, timetable)


@pytest.fixture
def subject(db_session, teacher):
    row = Subject(
        teacher_id=teacher.id, name="Physics", class_minutes=60, colour="#0E7A64"
    )
    row.students = [Student(name=n) for n in STUDENTS]
    db_session.add(row)
    db_session.commit()
    return row


def recording_transport(captured):
    def handler(request):
        captured.append(request.content.decode())
        if request.url.path == "/api/embed":
            vectors = [
                [0.0] * EMBED_DIMENSIONS for _ in json.loads(request.content)["input"]
            ]
            return httpx.Response(200, json={"embeddings": vectors})
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}})

    return httpx.MockTransport(handler)


def test_no_student_name_in_any_model_request(app, client, teacher, subject):
    sign_in(client)
    captured: list[str] = []
    app.extensions[TRANSPORT_KEY] = recording_transport(captured)
    text = (
        "Ask LEO martins and yusuf OKAFOR; mia chen, Leo, Okafor and (CHEN) agree. "
        "Mia's report on Yusuf. Aamina approved."
    )
    with app.test_request_context():
        from flask import g

        g.teacher = teacher
        client_ = model_client(teacher, subject)
        client_.generate(text, {"type": "object"})
        client_.chat([{"role": "user", "content": text}])
        client_.embed([text, "Okafor"])
        client_.close()
    assert len(captured) == 3
    terms = [t for name in STUDENTS for t in (name, *name.split())] + ["Aamina"]
    for body in captured:
        decoded = json.loads(body)
        flat = json.dumps(decoded, ensure_ascii=False)
        for term in terms:
            assert not re.search(
                rf"(?<!\w){re.escape(term)}(?!\w)", flat, re.IGNORECASE
            ), term
    assert "Student A" in captured[0]


def test_model_client_is_always_redacting(app, client, teacher, subject):
    config = OllamaConfig("http://ollama.test:11434", "e", "w")
    with app.test_request_context():
        assert isinstance(model_client(teacher, subject, config), RedactingClient)
        app.extensions[FACTORY_KEY] = lambda config, redactor: OllamaClient(config)
        with pytest.raises(RuntimeError):
            model_client(teacher, subject, config)
        del app.extensions[FACTORY_KEY]
        with pytest.raises(RuntimeError):
            model_client(teacher, subject, config)


def test_names_for_without_subject_covers_the_account(app, teacher, subject):
    with app.test_request_context():
        assert ai.names_for(teacher, None).redact("Leo Martins") == "Student A"
        assert ai.names_for(teacher, subject).redact("Mia") == "Student C"


def test_create_app_installs_factory(app):
    assert FACTORY_KEY in app.extensions


def test_no_ollama_client_outside_ai():
    allowed = {SOURCE / "ai" / "ollama.py", SOURCE / "ai" / "__init__.py"}
    for path in SOURCE.rglob("*.py"):
        body = path.read_text()
        if "OllamaClient" in body:
            assert path in allowed, path
        if "._inner" in body:
            assert path == SOURCE / "ai" / "redact.py", path
    init = (SOURCE / "ai" / "__init__.py").read_text()
    assert '"OllamaClient"' not in init
    assert "client_for" not in init


def test_settings_check_goes_through_redaction(client, teacher, monkeypatch):
    calls = []
    original = RedactingClient.health

    def health(self):
        calls.append(True)
        return original(self)

    monkeypatch.setattr(RedactingClient, "health", health)
    sign_in(client)
    token = csrf_token(client, "/settings")
    client.post("/settings/ollama/check", data={"csrf_token": token})
    assert calls


def test_cross_subject_roster_with_alike_spellings_is_redacted(
    app, client, db_session, teacher
):
    for name, student in (("Physics", "Weiß"), ("Maths", "Weiss")):
        row = Subject(
            teacher_id=teacher.id, name=name, class_minutes=60, colour="#0E7A64"
        )
        row.students = [Student(name=student)]
        db_session.add(row)
    db_session.commit()
    captured: list[str] = []
    app.extensions[TRANSPORT_KEY] = recording_transport(captured)
    with app.test_request_context():
        from flask import g

        g.teacher = teacher
        client_ = model_client(teacher)
        client_.generate("Weiß met Weiss", {"type": "object"})
        client_.close()
    assert len(captured) == 1
    flat = json.dumps(json.loads(captured[0]), ensure_ascii=False)
    assert "Weiß" not in flat and "Weiss" not in flat
    assert "Student A" in flat and "Student B" in flat


def chat_transport(captured, models):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": m, "model": m} for m in models]}
            )
        captured.append(request.content.decode())
        if request.url.path == "/api/embed":
            vectors = [
                [0.0] * EMBED_DIMENSIONS for _ in json.loads(request.content)["input"]
            ]
            return httpx.Response(200, json={"embeddings": vectors})
        content = json.dumps(
            {
                "sentences": [
                    {"text": "Ask Student A to explain.", "passages": ["P1"]}
                ],
                "supported": True,
                "reason": "ok",
            }
        )
        return httpx.Response(200, json={"message": {"content": content}})

    return httpx.MockTransport(handler)


def test_student_name_typed_in_chat_never_reaches_model(
    app, client, db_session, teacher, timetable
):
    _, lessons = approved_with_lessons(db_session, timetable)
    sign_in(client)
    captured: list[str] = []
    with app.app_context():
        config = ai.config_for(teacher)
    app.extensions[TRANSPORT_KEY] = chat_transport(
        captured, [config.embed_model, config.write_model]
    )
    item_id = lessons.items[0].id
    page = f"/subjects/{timetable.id}/today/lessons/{item_id}"
    client.post(
        f"{page}/chat",
        data={
            "question": "How should Leo Martins and mia explain Okafor's result?",
            "csrf_token": csrf_token(client, page),
        },
    )
    while jobs.run_once(app):
        pass
    assert captured
    assert any("messages" in body for body in captured)
    terms = [t for name in STUDENTS for t in (name, *name.split())]
    for body in captured:
        flat = json.dumps(json.loads(body), ensure_ascii=False)
        for term in terms:
            assert not re.search(
                rf"(?<!\w){re.escape(term)}(?!\w)", flat, re.IGNORECASE
            ), term
    assert any("Student A" in body for body in captured)
