import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from chalkboard import jobs, trivia
from chalkboard.ai.retrieve import Passage
from chalkboard.models import (
    Chunk,
    Citation,
    Job,
    Sentence,
    Student,
    Teacher,
    TriviaSet,
)
from tests.conftest import csrf_token
from tests.test_assistant import lesson, run_all, server
from tests.test_chapters import SUPPORTED
from tests.test_chapters_page import html
from tests.test_today_page import at, signed_in, sourced, subject, timetable, today_url

# Reuse the fixtures of the assistant and Today page tests.
fixtures = (signed_in, sourced, subject, timetable, lesson, server)


def qs(*pairs, label="P1"):
    return {
        "questions": [{"question": q, "answer": a, "passage": label} for q, a in pairs]
    }


def three():
    return qs(
        ("What is force?", "A push."),
        ("What is mass?", "Matter."),
        ("What is g?", "9.8."),
    )


def page_url(subject, item_id, tail=""):
    return today_url(subject, f"/lessons/{item_id}{tail}")


def post(client, subject, item_id, tail, **data):
    token = csrf_token(client, page_url(subject, item_id))
    return client.post(
        page_url(subject, item_id, tail), data={"csrf_token": token, **data}
    )


def write(app, client, subject, item_id, server, count=3, generated=None):
    server.generated = generated or [three(), SUPPORTED, SUPPORTED, SUPPORTED]
    response = post(client, subject, item_id, "/trivia", count=count)
    run_all(app)
    return response


def sets(db_session):
    db_session.expire_all()
    return list(db_session.scalars(select(TriviaSet).order_by(TriviaSet.number)))


def prompts(server):
    return [call[0] for call in server.calls if isinstance(call[0], str)]


@pytest.fixture
def after_class(monkeypatch):
    at(monkeypatch, 2026, 11, 27, 12)


def test_lesson_passages_are_the_lessons_citations(db_session, timetable, lesson):
    cited = set(
        db_session.scalars(
            select(Citation.chunk_id)
            .join(Sentence, Sentence.id == Citation.sentence_id)
            .where(Sentence.item_id == lesson[0])
        )
    )
    passages = trivia.lesson_passages(db_session, lesson[0])
    assert {p.chunk_id for p in passages} == cited
    assert [p.label for p in passages] == [f"P{i + 1}" for i in range(len(passages))]


def test_parse_questions_drops_bad_entries():
    passages = [Passage("P1", 10, 2, "text", None, None)]
    answer = {
        "questions": [
            {"question": "Kept?", "answer": "Yes.", "passage": " p1 "},
            {"question": "Unknown label?", "answer": "No.", "passage": "P9"},
            {"question": "No answer?", "answer": " ", "passage": "P1"},
            {"question": "KEPT?", "answer": "Dup.", "passage": "P1"},
            {"question": "Asked before?", "answer": "Yes.", "passage": "P1"},
            {"question": "What did Student A say?", "answer": "Hi.", "passage": "P1"},
            {"question": "Who spoke?", "answer": "Teacher B.", "passage": "P1"},
            {"question": "x" * 301, "answer": "Long.", "passage": "P1"},
            "not a dict",
        ]
    }
    [kept] = trivia.parse_questions(answer, passages, ["asked before?"])
    assert kept == {
        "question": "Kept?",
        "answer": "Yes.",
        "chunk_id": 10,
        "source_number": 2,
        "edited": False,
    }
    assert trivia.parse_questions({"questions": "no"}, passages, []) == []
    assert trivia.parse_questions(None, passages, []) == []


def test_small_helpers():
    assert [trivia.minutes_for(n) for n in (3, 4, 5)] == [4, 5, 7]
    questions = [{"question": "A?", "answer": "a"}, {"question": "B?", "answer": "b"}]
    assert trivia.board_text(questions) == "1. A?\n2. B?"


def test_write_trivia_saves_chosen_count(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    response = write(app, signed_in, timetable, lesson[0], server)
    assert response.status_code == 303
    assert response.headers["Location"].endswith(
        f"/lessons/{lesson[0]}?trivia=1#trivia"
    )
    [saved] = sets(db_session)
    assert saved.number == 1 and len(saved.questions) == 3
    for q in saved.questions:
        assert q["answer"] and q["chunk_id"] and q["source_number"]
    page = html(signed_in, page_url(timetable, lesson[0]))
    assert "Q3" in page and "Answer:" in page
    assert 'aria-label="Citation, source' in page


@pytest.mark.parametrize("count", [4, 5])
def test_write_trivia_counts_4_and_5(
    app, signed_in, db_session, timetable, lesson, server, after_class, count
):
    pairs = [(f"Question {i}?", f"Answer {i}.") for i in range(count)]
    write(
        app,
        signed_in,
        timetable,
        lesson[0],
        server,
        count=count,
        generated=[qs(*pairs)] + [SUPPORTED] * count,
    )
    [saved] = sets(db_session)
    assert len(saved.questions) == count


def test_write_trivia_tops_up(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(
        app,
        signed_in,
        timetable,
        lesson[0],
        server,
        generated=[
            qs(("What is force?", "A push."), ("What is mass?", "Matter.")),
            SUPPORTED,
            SUPPORTED,
            qs(("What is g?", "9.8.")),
            SUPPORTED,
        ],
    )
    [saved] = sets(db_session)
    assert len(saved.questions) == 3
    asked = [p for p in prompts(server) if "quiz questions" in p]
    assert "Do not repeat" not in asked[0] and "Do not repeat" in asked[1]
    assert "What is force?" in asked[1]


def test_write_trivia_fails_when_short(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(
        app,
        signed_in,
        timetable,
        lesson[0],
        server,
        generated=[
            qs(("What is force?", "A push.")),
            SUPPORTED,
            qs(("What is mass?", "Matter.")),
            SUPPORTED,
        ],
    )
    job = db_session.scalars(select(Job).where(Job.kind == trivia.WRITE_KIND)).one()
    assert (job.state, job.error) == ("failed", trivia.NOT_ENOUGH)
    assert sets(db_session) == []
    page = html(signed_in, page_url(timetable, lesson[0]))
    assert trivia.NOT_ENOUGH.replace("'", "&#39;") in page and 'role="alert"' in page


def test_write_fails_without_sources(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    db_session.query(Citation).update({Citation.chunk_id: None})
    db_session.commit()
    write(app, signed_in, timetable, lesson[0], server)
    job = db_session.scalars(select(Job).where(Job.kind == trivia.WRITE_KIND)).one()
    assert job.error == trivia.NO_SOURCES


def test_write_rejects_bad_count(
    signed_in, db_session, timetable, lesson, server, after_class
):
    response = post(signed_in, timetable, lesson[0], "/trivia", count=7)
    assert response.status_code == 400
    assert db_session.scalar(select(func.count(Job.id))) == 0


def test_edit_creates_new_version(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    [first] = sets(db_session)
    response = post(
        signed_in,
        timetable,
        lesson[0],
        "/trivia/questions/1",
        set_id=first.id,
        question="  What is   inertia? ",
        answer="Resistance to change.",
    )
    assert response.status_code == 303
    one, two = sets(db_session)
    assert (two.number, two.job_id) == (2, None)
    assert two.questions[1]["question"] == "What is inertia?"
    assert two.questions[1]["edited"] is True
    assert two.questions[0] == one.questions[0]
    assert one.questions[1]["question"] == "What is mass?"
    assert one.questions[1]["edited"] is False
    assert "Edited" in html(signed_in, page_url(timetable, lesson[0]))


def test_edit_with_stale_set_id_saves_nothing(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    response = post(
        signed_in,
        timetable,
        lesson[0],
        "/trivia/questions/0",
        set_id=999,
        question="Q?",
        answer="A.",
    )
    assert response.status_code == 303
    assert len(sets(db_session)) == 1
    page = html(signed_in, page_url(timetable, lesson[0], "?trivia=1"))
    assert "This trivia changed since you opened it" in page


def test_edit_conflict_shows_stale_message(
    app, signed_in, db_session, timetable, lesson, server, after_class, monkeypatch
):
    write(app, signed_in, timetable, lesson[0], server)
    [first] = sets(db_session)

    def conflict(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(trivia, "edit_question", conflict)
    response = post(
        signed_in,
        timetable,
        lesson[0],
        "/trivia/questions/0",
        set_id=first.id,
        question="Q?",
        answer="A.",
    )
    assert response.status_code == 303
    assert len(sets(db_session)) == 1
    page = html(signed_in, page_url(timetable, lesson[0], "?trivia=1"))
    assert "This trivia changed since you opened it" in page


def test_poll_pauses_while_editing(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    [first] = sets(db_session)
    server.generated = []
    post(signed_in, timetable, lesson[0], "/trivia/questions/0/new", set_id=first.id)
    assert "hx-trigger" in html(signed_in, page_url(timetable, lesson[0], "?trivia=1"))
    editing = html(signed_in, page_url(timetable, lesson[0], "?trivia=1&edit=0"))
    assert "hx-trigger" not in editing


def test_edit_rejects_empty_and_bad_index(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    [first] = sets(db_session)
    empty = post(
        signed_in,
        timetable,
        lesson[0],
        "/trivia/questions/0",
        set_id=first.id,
        question="",
        answer="A.",
    )
    assert "edit=0" in empty.headers["Location"]
    page = html(signed_in, page_url(timetable, lesson[0], "?trivia=1&edit=0"))
    assert trivia.QUESTION_EMPTY in page and 'name="question"' in page
    missing = post(
        signed_in,
        timetable,
        lesson[0],
        "/trivia/questions/9",
        set_id=first.id,
        question="Q?",
        answer="A.",
    )
    assert missing.status_code == 404
    assert len(sets(db_session)) == 1


def test_new_question_replaces_only_that_one(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    [first] = sets(db_session)
    server.generated = [qs(("What is weight?", "Force of gravity.")), SUPPORTED]
    post(signed_in, timetable, lesson[0], "/trivia/questions/1/new", set_id=first.id)
    run_all(app)
    one, two = sets(db_session)
    assert two.number == 2
    assert two.questions[0] == one.questions[0]
    assert two.questions[2] == one.questions[2]
    assert two.questions[1]["question"] == "What is weight?"
    assert two.questions[1]["edited"] is False
    assert one.questions[1]["question"] == "What is mass?"
    asked = [p for p in prompts(server) if "quiz questions" in p][-1]
    assert all(q["question"] in asked for q in one.questions)


def test_new_question_keeps_edit_made_while_running(
    app, signed_in, db_session, timetable, lesson, server, after_class, monkeypatch
):
    write(app, signed_in, timetable, lesson[0], server)
    [first] = sets(db_session)
    server.generated = [qs(("What is weight?", "Force of gravity.")), SUPPORTED]
    post(signed_in, timetable, lesson[0], "/trivia/questions/1/new", set_id=first.id)
    draft = trivia.draft_questions

    def edit_then_draft(*args, **kwargs):
        found = draft(*args, **kwargs)
        trivia.edit_question(db_session, first, 0, "Edited Q?", "Edited A.")
        db_session.commit()
        return found

    monkeypatch.setattr(trivia, "draft_questions", edit_then_draft)
    run_all(app)
    *_, replaced = sets(db_session)
    assert replaced.number == 3
    assert replaced.questions[0]["question"] == "Edited Q?"
    assert replaced.questions[1]["question"] == "What is weight?"


def test_new_question_with_stale_set_id_queues_nothing(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    response = post(
        signed_in, timetable, lesson[0], "/trivia/questions/1/new", set_id=999
    )
    assert response.status_code == 303
    assert db_session.scalar(select(func.count(Job.id))) == 1
    page = html(signed_in, page_url(timetable, lesson[0], "?trivia=1"))
    assert "This trivia changed since you opened it" in page


def test_new_question_fails_cleanly(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    [first] = sets(db_session)
    server.generated = []
    post(signed_in, timetable, lesson[0], "/trivia/questions/0/new", set_id=first.id)
    run_all(app)
    job = db_session.scalars(select(Job).where(Job.kind == trivia.REPLACE_KIND)).one()
    assert job.error == trivia.NO_NEW
    assert len(sets(db_session)) == 1


def test_new_question_ignored_for_bad_index_or_no_set(
    signed_in, db_session, timetable, lesson, server, after_class
):
    post(signed_in, timetable, lesson[0], "/trivia/questions/0/new", set_id=1)
    assert db_session.scalar(select(func.count(Job.id))) == 0


def test_second_job_is_not_queued_while_busy(
    signed_in, db_session, timetable, lesson, server, after_class
):
    post(signed_in, timetable, lesson[0], "/trivia", count=3)
    post(signed_in, timetable, lesson[0], "/trivia", count=3)
    assert db_session.scalar(select(func.count(Job.id))) == 1


def test_trivia_jobs_are_idempotent(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    job = db_session.scalars(select(Job).where(Job.kind == trivia.WRITE_KIND)).one()
    ctx = jobs.JobContext(
        db_session, job, db_session.get(Teacher, job.teacher_id), job.claims
    )
    trivia.write_trivia(ctx)
    assert db_session.scalar(select(func.count(TriviaSet.id))) == 1


def test_card_appears_after_class_time(
    signed_in, timetable, lesson, server, monkeypatch
):
    url = page_url(timetable, lesson[0])
    at(monkeypatch, 2026, 11, 24, 8)
    before = html(signed_in, url)
    assert 'id="trivia"' not in before and "Trivia for class</a>" in before
    at(monkeypatch, 2026, 11, 24, 10, 1)
    after = html(signed_in, url)
    for text in ('id="trivia"', "Write trivia", "Class taught"):
        assert text in after
    assert "Trivia for class</a>" not in after


def test_header_button_opens_card_before_class(
    signed_in, timetable, lesson, server, monkeypatch
):
    at(monkeypatch, 2026, 11, 24, 8)
    assert 'id="trivia"' in html(signed_in, page_url(timetable, lesson[0], "?trivia=1"))


def test_progress_has_role_status(signed_in, timetable, lesson, server, after_class):
    post(signed_in, timetable, lesson[0], "/trivia", count=3)
    for url in (
        page_url(timetable, lesson[0]),
        page_url(timetable, lesson[0], "/trivia"),
    ):
        page = html(signed_in, url)
        assert 'role="status"' in page and 'hx-trigger="every 2s"' in page


def test_write_disabled_when_ollama_down(
    signed_in, db_session, teacher, timetable, lesson, server, after_class
):
    teacher.ollama_status = "unreachable"
    db_session.commit()
    page = html(signed_in, page_url(timetable, lesson[0]))
    assert "ollama-banner" in page
    assert '<button class="btn btn-primary" disabled>Write trivia' in page
    assert post(signed_in, timetable, lesson[0], "/trivia", count=3).status_code == 303
    assert db_session.scalar(select(func.count(Job.id))) == 0


def test_copy_for_the_board_has_questions_only(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    write(app, signed_in, timetable, lesson[0], server)
    page = html(signed_in, page_url(timetable, lesson[0]))
    board = page.split('data-copy="')[1].split('"')[0]
    assert board.startswith("1. What is force?")
    assert "3. What is g?" in board
    assert "A push." not in board and "Answer" not in board


def test_no_student_names_sent(
    app, signed_in, db_session, timetable, lesson, server, after_class
):
    timetable.students = [Student(name="Mia Chen")]
    chunk = db_session.scalars(
        select(Chunk)
        .join(Citation, Citation.chunk_id == Chunk.id)
        .join(Sentence, Sentence.id == Citation.sentence_id)
        .where(Sentence.item_id == lesson[0])
    ).first()
    chunk.text = f"{chunk.text} Mia Chen MARKER-7Q measured it."
    db_session.commit()
    write(app, signed_in, timetable, lesson[0], server)
    asked = prompts(server)
    assert any("MARKER-7Q" in p for p in asked)
    assert not any("Mia Chen" in p for p in asked)
