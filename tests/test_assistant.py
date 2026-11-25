import pytest
from sqlalchemy import func, select

from chalkboard import assistant, jobs
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.ai.retrieve import Passage
from chalkboard.ai.write import DraftSentence
from chalkboard.models import ChatMessage, Chunk, Job, Student, Teacher
from tests.conftest import csrf_token
from tests.test_chapters import SUPPORTED
from tests.test_chapters_page import approved_with_lessons, html
from tests.test_today_page import signed_in, sourced, subject, timetable, today_url

# Reuse the fixtures of the Today page tests.
fixtures = (signed_in, sourced, subject, timetable)

UNSUPPORTED = {"supported": False, "reason": "It says something else"}


def sentences(*texts, labels=("P1",)):
    return {"sentences": [{"text": text, "passages": list(labels)} for text in texts]}


@pytest.fixture
def server(app):
    fake = FakeOllama()
    app.extensions[FACTORY_KEY] = lambda config, redactor: RedactingClient(
        fake, redactor
    )
    return fake


@pytest.fixture
def lesson(db_session, timetable):
    _, lessons = approved_with_lessons(db_session, timetable)
    return [item.id for item in lessons.items]


def chat_url(subject, item_id, tail=""):
    return today_url(subject, f"/lessons/{item_id}/chat{tail}")


def post_question(client, subject, item_id, text, htmx=False):
    token = csrf_token(client, today_url(subject, f"/lessons/{item_id}"))
    return client.post(
        chat_url(subject, item_id),
        data={"question": text, "csrf_token": token},
        headers={"HX-Request": "true"} if htmx else {},
    )


def run_all(app):
    while jobs.run_once(app):
        pass


def messages(db_session):
    return list(db_session.scalars(select(ChatMessage).order_by(ChatMessage.id)))


def test_ask_saves_question_and_enqueues_job(
    signed_in, db_session, timetable, lesson, server
):
    response = post_question(signed_in, timetable, lesson[0], "  What is force? ")
    assert response.status_code == 303
    assert response.headers["Location"].endswith(f"/lessons/{lesson[0]}#assistant")
    [question] = messages(db_session)
    assert (question.role, question.text, question.item_id) == (
        "teacher",
        "What is force?",
        lesson[0],
    )
    job = db_session.get(Job, question.job_id)
    assert job.kind == assistant.ANSWER_KIND
    assert job.payload == {"question_id": question.id}


def test_answer_from_sources_has_chip_and_citations(
    app, signed_in, db_session, timetable, lesson, server
):
    server.generated = [sentences("A force changes motion."), SUPPORTED]
    post_question(signed_in, timetable, lesson[0], "What is force?", htmx=True)
    run_all(app)
    [_, answer] = messages(db_session)
    assert answer.label == "from_sources"
    assert answer.parts[0]["text"] == "A force changes motion."
    page = html(signed_in, today_url(timetable, f"/lessons/{lesson[0]}"))
    assert "From your sources" in page
    assert "A force changes motion." in page
    assert 'class="cite" href=' in page
    assert 'aria-label="Citation, source 2"' in page
    assert 'class="typing"' not in page


def test_unsupported_answer_is_not_in_your_sources(
    app, signed_in, db_session, timetable, lesson, server
):
    server.generated = [sentences("Force is a kind of cheese."), UNSUPPORTED]
    post_question(signed_in, timetable, lesson[0], "What is force?")
    run_all(app)
    [_, answer] = messages(db_session)
    assert (answer.label, answer.parts) == ("not_in_sources", [])
    page = html(signed_in, today_url(timetable, f"/lessons/{lesson[0]}"))
    assert '<span class="chip chip-flag">Not in your sources</span>' in page
    assert "bubble-missing" in page
    assert "<strong>Sources + web</strong>" in page
    assert "cheese" not in page


def test_no_passages_is_not_in_your_sources(
    app, signed_in, db_session, timetable, lesson, server
):
    db_session.query(Chunk).filter(Chunk.text != "").delete()
    db_session.commit()
    post_question(signed_in, timetable, lesson[0], "What is force?")
    run_all(app)
    [_, answer] = messages(db_session)
    assert answer.label == "not_in_sources"
    assert answer.text == assistant.NOT_IN_SOURCES.format(subject="Physics")
    assert server.generated == [] and len(server.calls) == 1  # only the embedding


def test_answer_restores_placeholders(
    app, signed_in, db_session, timetable, lesson, server
):
    timetable.students = [Student(name="Mia Chen")]
    db_session.commit()
    server.generated = [sentences("Ask Student A to explain."), SUPPORTED]
    post_question(signed_in, timetable, lesson[0], "Who should explain force?")
    run_all(app)
    [_, answer] = messages(db_session)
    assert answer.text == "Ask Mia Chen to explain."
    assert answer.parts[0]["text"] == "Ask Mia Chen to explain."


def test_job_is_idempotent(app, signed_in, db_session, timetable, lesson, server):
    server.generated = [sentences("A force changes motion."), SUPPORTED]
    post_question(signed_in, timetable, lesson[0], "What is force?")
    run_all(app)
    job = db_session.scalars(select(Job).where(Job.kind == assistant.ANSWER_KIND)).one()
    ctx = jobs.JobContext(
        db_session, job, db_session.get(Teacher, job.teacher_id), job.claims
    )
    assistant.answer_question(ctx)
    assert db_session.scalar(select(func.count(ChatMessage.id))) == 2


def test_answer_parts_skips_flagged_and_uncited():
    class Client:
        class redactor:
            restore = staticmethod(lambda text: text)

    passages = [Passage("P1", 10, 3, "text", None, None)]
    found = [
        DraftSentence("kept", (10, 99)),
        DraftSentence("flagged", (10,), check_flag=True),
        DraftSentence("unknown chunk", (99,)),
    ]
    assert assistant.answer_parts(Client, found, passages) == [
        {"text": "kept", "cites": [[10, 3]]}
    ]


def test_pending_question_shows_typing_dots(
    app, signed_in, db_session, timetable, lesson, server
):
    server.generated = [sentences("A force changes motion."), SUPPORTED]
    post_question(signed_in, timetable, lesson[0], "What is force?")
    [question] = messages(db_session)
    page = html(signed_in, today_url(timetable, f"/lessons/{lesson[0]}"))
    assert 'class="typing"' in page and 'hx-trigger="every 2s"' in page
    poll = html(signed_in, chat_url(timetable, lesson[0], f"/{question.id}"))
    assert 'class="typing"' in poll and "Writing an answer" in poll
    run_all(app)
    done = html(signed_in, chat_url(timetable, lesson[0], f"/{question.id}"))
    assert "From your sources" in done and "hx-get" not in done


def test_waiting_job_says_so(signed_in, db_session, timetable, lesson, server):
    post_question(signed_in, timetable, lesson[0], "What is force?")
    [question] = messages(db_session)
    db_session.get(Job, question.job_id).state = "waiting_ollama"
    db_session.commit()
    poll = html(signed_in, chat_url(timetable, lesson[0], f"/{question.id}"))
    assert "Waiting for the Ollama server" in poll and "hx-get" in poll


def test_failed_job_shows_error(signed_in, db_session, timetable, lesson, server):
    post_question(signed_in, timetable, lesson[0], "What is force?")
    [question] = messages(db_session)
    job = db_session.get(Job, question.job_id)
    job.state, job.error = "failed", "The model gave up"
    db_session.commit()
    poll = html(signed_in, chat_url(timetable, lesson[0], f"/{question.id}"))
    assert "The model gave up" in poll and "hx-get" not in poll
    page = html(signed_in, today_url(timetable, f"/lessons/{lesson[0]}"))
    assert "job-error" in page and 'class="typing"' not in page


def test_chat_disabled_when_ollama_down(
    signed_in, db_session, teacher, timetable, lesson, server
):
    teacher.ollama_status = "unreachable"
    db_session.commit()
    page = html(signed_in, today_url(timetable, f"/lessons/{lesson[0]}"))
    assert "ollama-banner" in page and "Can't reach the Ollama server" in page
    assert 'name="question"' in page
    assert 'autocomplete="off" value="" disabled' in page
    assert 'aria-label="Send" disabled' in page
    assert "Warm-up 1." in page and 'id="sources-title"' in page
    assert post_question(signed_in, timetable, lesson[0], "Hi?").status_code == 303
    htmx = post_question(signed_in, timetable, lesson[0], "Hi?", htmx=True)
    assert "ollama-banner" in htmx.get_data(as_text=True)
    assert messages(db_session) == []
    assert db_session.scalar(select(func.count(Job.id))) == 0


@pytest.mark.parametrize(
    ("text", "error"),
    [("   ", assistant.QUESTION_EMPTY), ("x" * 501, assistant.QUESTION_TOO_LONG)],
)
def test_empty_and_long_questions_rejected(
    signed_in, db_session, timetable, lesson, server, text, error
):
    response = post_question(signed_in, timetable, lesson[0], text, htmx=True)
    assert response.status_code == 200
    assert error in response.get_data(as_text=True)
    assert post_question(signed_in, timetable, lesson[0], text).status_code == 303
    assert messages(db_session) == []


def test_messages_are_per_lesson(app, signed_in, db_session, timetable, lesson, server):
    server.generated = [sentences("A force changes motion."), SUPPORTED]
    post_question(signed_in, timetable, lesson[0], "Question for the first class")
    run_all(app)
    first = html(signed_in, today_url(timetable, f"/lessons/{lesson[0]}"))
    second = html(signed_in, today_url(timetable, f"/lessons/{lesson[1]}"))
    assert "Question for the first class" in first
    assert "A force changes motion." in first
    assert "Question for the first class" not in second
    assert "bubble-teacher" not in second


def test_chat_routes_404_for_other_lessons(
    app, signed_in, db_session, timetable, lesson, server
):
    server.generated = [sentences("A force changes motion."), SUPPORTED]
    post_question(signed_in, timetable, lesson[0], "What is force?")
    run_all(app)
    question, answer = messages(db_session)
    chunk_id = answer.parts[0]["cites"][0][0]
    assert (
        signed_in.get(chat_url(timetable, lesson[1], f"/{question.id}")).status_code
        == 404
    )
    assert (
        signed_in.get(chat_url(timetable, lesson[0], f"/{answer.id}")).status_code
        == 404
    )
    assert signed_in.get(chat_url(timetable, lesson[0], "/9999")).status_code == 404
    other = chat_url(timetable, lesson[1], f"/{answer.id}/chunks/{chunk_id}")
    assert signed_in.get(other).status_code == 404
    wrong_chunk = chat_url(
        timetable, lesson[0], f"/{answer.id}/chunks/{chunk_id + 1000}"
    )
    assert signed_in.get(wrong_chunk).status_code == 404
    as_teacher = chat_url(timetable, lesson[0], f"/{question.id}/chunks/{chunk_id}")
    assert signed_in.get(as_teacher).status_code == 404
    token = csrf_token(signed_in, today_url(timetable, f"/lessons/{lesson[0]}"))
    missing = signed_in.post(
        chat_url(timetable, 987654), data={"question": "x", "csrf_token": token}
    )
    assert missing.status_code == 404


def test_chat_passage_shows_chunk(
    app, signed_in, db_session, timetable, lesson, server
):
    server.generated = [sentences("A force changes motion."), SUPPORTED]
    post_question(signed_in, timetable, lesson[0], "What is force?")
    run_all(app)
    _, answer = messages(db_session)
    chunk_id = answer.parts[0]["cites"][0][0]
    url = chat_url(timetable, lesson[0], f"/{answer.id}/chunks/{chunk_id}")
    panel = html(signed_in, url)
    assert 'class="passage-quote"' in panel
    text = db_session.get(Chunk, chunk_id).text
    assert text in panel and "Source 2, passage" in panel
    db_session.query(Chunk).filter(Chunk.id == chunk_id).delete()
    db_session.commit()
    assert "Source 2 · source deleted" in html(signed_in, url)
