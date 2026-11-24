import io

import pytest
from markupsafe import escape
from sqlalchemy import func, select

from chalkboard import jobs
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.models import Job, Source, Subject
from chalkboard.subjects import board_name, syllabus_display
from chalkboard.syllabus import unit_rows, use_as_syllabus
from tests.conftest import csrf_token, sign_in
from tests.pdfs import make_pdf
from tests.test_sources import upload
from tests.test_subjects import valid

PAGES = ["Unit I Kinematics", "Unit II Laws of Motion", "Unit III Gravitation"]
SYLLABUS_PDF = make_pdf(PAGES)
PDF_ONLY = "Upload the syllabus as a PDF"


@pytest.fixture
def data(app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.extensions["chalkboard.settings"], "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def server(app):
    fake = FakeOllama()
    app.extensions[FACTORY_KEY] = lambda config, redactor: RedactingClient(
        fake, redactor
    )
    return fake


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def make_subject(db_session, teacher, **fields):
    values = {
        "teacher_id": teacher.id,
        "name": "Physics",
        "class_minutes": 60,
        "colour": "#0E7A64",
        "board": "cbse",
        "grade": "Class 11",
    }
    row = Subject(**{**values, **fields})
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def subject(db_session, teacher):
    return make_subject(db_session, teacher)


def run_all(app):
    while jobs.run_once(app):
        pass


def text(response):
    return response.get_data(as_text=True)


def url(subject, tail=""):
    return f"/subjects/{subject.id}/sources{tail}"


def post_sources(client, subject, tail, **form):
    token = csrf_token(client, url(subject))
    return client.post(url(subject, tail), data={"csrf_token": token, **form})


def post_syllabus_file(client, subject, name="syllabus.pdf", content=SYLLABUS_PDF):
    token = csrf_token(client, url(subject))
    return client.post(
        url(subject, "/syllabus/upload"),
        data={"csrf_token": token, "file": (io.BytesIO(content), name)},
        content_type="multipart/form-data",
    )


def post_subject(client, path, file=None, **fields):
    token = csrf_token(client, "/subjects/new")
    data = {"csrf_token": token, **valid(**fields)}
    if file is not None:
        data["syllabus_file"] = (io.BytesIO(file[1]), file[0])
    return client.post(path, data=data, content_type="multipart/form-data")


def settings(client, subject, file=None, **fields):
    fields = {"name": subject.name, "class_minutes": "60", **fields}
    return post_subject(client, f"/subjects/{subject.id}/settings", file, **fields)


def syllabus_of(db_session, subject):
    db_session.expire_all()
    return db_session.scalar(
        select(Source).where(Source.subject_id == subject.id, Source.is_syllabus)
    )


def test_board_name():
    assert board_name("cbse", None) == "CBSE"
    assert board_name("state", "Maharashtra State Board") == "Maharashtra State Board"
    assert board_name("state", None) == ""
    assert board_name("none", None) == ""


def test_syllabus_display():
    assert syllabus_display("remove", "cur.pdf", {}) == ""
    assert syllabus_display("7", "cur.pdf", {7: "a.pdf"}) == "a.pdf"
    assert syllabus_display("8", "cur.pdf", {7: "a.pdf"}) == ""
    assert syllabus_display("", "cur.pdf", {7: "a.pdf"}) == "cur.pdf"


def test_no_syllabus_line_with_both_actions(
    app, signed_in, subject, db_session, data, server
):
    upload(app, db_session, subject)
    html = text(signed_in.get(url(subject)))
    assert "No CBSE syllabus yet." in html
    assert "The overview is planned from your sources alone" in html
    assert "Add syllabus PDF" in html
    assert "Choose from sources" in html


def test_no_syllabus_line_empty_has_upload_only(signed_in, subject):
    html = text(signed_in.get(url(subject)))
    assert "No CBSE syllabus yet." in html
    assert "Add syllabus PDF" in html
    assert "Choose from sources" not in html


def test_no_syllabus_line_hidden_for_none_and_when_set(
    app, signed_in, db_session, teacher, data, server
):
    plain = make_subject(db_session, teacher, name="Maths", board="none")
    assert "syllabus yet" not in text(signed_in.get(url(plain)))
    cbse = make_subject(db_session, teacher, name="Chem")
    source = upload(app, db_session, cbse)
    use_as_syllabus(db_session, cbse, source)
    assert "syllabus yet" not in text(signed_in.get(url(cbse)))


def test_s03_upload_marks_and_chains_detection(
    app, signed_in, subject, db_session, data, server
):
    assert post_syllabus_file(signed_in, subject).status_code == 303
    source = syllabus_of(db_session, subject)
    assert source.is_syllabus
    assert db_session.get(Job, source.job_id).kind == "ingest_source"
    run_all(app)
    db_session.expire_all()
    assert [u.number for u in unit_rows(db_session, source.id)] == [1, 2, 3]


def test_s03_upload_refuses_non_pdf(signed_in, subject, db_session, data):
    response = post_syllabus_file(signed_in, subject, "notes.txt", b"hello world")
    assert response.status_code == 303
    assert PDF_ONLY in text(signed_in.get(url(subject)))
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0


def test_s03_upload_board_none_400(signed_in, db_session, teacher, data):
    plain = make_subject(db_session, teacher, board="none")
    assert post_syllabus_file(signed_in, plain).status_code == 400
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0


def test_choose_from_sources_modal_lists_rows_and_marks(
    app, signed_in, subject, db_session, data, server
):
    source = upload(app, db_session, subject, name="textbook.txt")
    html = text(signed_in.get(url(subject, "/syllabus/choose")))
    assert "Choose the syllabus" in html
    assert "textbook.txt" in html
    assert post_sources(signed_in, subject, f"/{source.id}/syllabus").status_code == 303
    assert syllabus_of(db_session, subject).id == source.id


def test_choose_redirects_when_syllabus_set(
    app, signed_in, subject, db_session, data, server
):
    use_as_syllabus(db_session, subject, upload(app, db_session, subject))
    assert signed_in.get(url(subject, "/syllabus/choose")).status_code == 303


def test_new_subject_with_syllabus_pdf(signed_in, db_session, teacher, data, server):
    response = post_subject(
        signed_in, "/subjects/new", ("syllabus.pdf", SYLLABUS_PDF), board="cbse"
    )
    assert response.status_code == 302
    created = db_session.scalar(select(Subject).where(Subject.name == "Chemistry"))
    source = syllabus_of(db_session, created)
    assert source.name == "syllabus.pdf"
    assert db_session.get(Job, source.job_id).kind == "ingest_source"


def test_new_subject_non_pdf_refused_nothing_saved(signed_in, db_session, data):
    response = post_subject(signed_in, "/subjects/new", ("notes.txt", b"hi"))
    assert response.status_code == 400
    assert PDF_ONLY in text(response)
    assert db_session.scalar(select(func.count()).select_from(Subject)) == 0
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0


def test_new_subject_board_none_ignores_file(signed_in, db_session, data):
    response = post_subject(
        signed_in, "/subjects/new", ("syllabus.pdf", SYLLABUS_PDF), board="none"
    )
    assert response.status_code == 302
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0


def test_settings_upload_saves_marked_source(
    signed_in, subject, db_session, data, server
):
    response = settings(signed_in, subject, ("syllabus.pdf", SYLLABUS_PDF))
    assert response.status_code == 302
    assert syllabus_of(db_session, subject).name == "syllabus.pdf"


def test_settings_non_pdf_refused_keeps_settings(signed_in, subject, db_session, data):
    response = settings(signed_in, subject, ("notes.txt", b"hi"), name="Renamed")
    assert response.status_code == 400
    assert PDF_ONLY in text(response)
    db_session.expire_all()
    assert db_session.get(Subject, subject.id).name == "Physics"


def test_settings_choose_source_marks_it(
    app, signed_in, subject, db_session, data, server
):
    source = upload(app, db_session, subject)
    assert settings(signed_in, subject, syllabus=str(source.id)).status_code == 302
    assert syllabus_of(db_session, subject).id == source.id


def test_settings_choose_foreign_source_refused(
    app, signed_in, subject, db_session, teacher, data, server
):
    other = make_subject(db_session, teacher, name="Chem")
    foreign = upload(app, db_session, other)
    response = settings(signed_in, subject, syllabus=str(foreign.id))
    assert response.status_code == 400
    assert str(escape("Choose one of this subject's sources")) in text(response)
    assert syllabus_of(db_session, subject) is None


def test_settings_remove_unmarks_keeps_source(
    app, signed_in, subject, db_session, data, server
):
    source = upload(app, db_session, subject)
    use_as_syllabus(db_session, subject, source)
    run_all(app)
    assert settings(signed_in, subject, syllabus="remove").status_code == 302
    assert syllabus_of(db_session, subject) is None
    kept = db_session.get(Source, source.id)
    assert kept.number is not None
    html = text(signed_in.get(url(subject)))
    assert 'class="syllabus-row"' not in html
    assert kept.name in html


def test_settings_board_change_unmarks(
    app, signed_in, subject, db_session, data, server
):
    source = upload(app, db_session, subject, name="syllabus.txt")
    use_as_syllabus(db_session, subject, source)
    assert settings(signed_in, subject, board="icse").status_code == 302
    assert syllabus_of(db_session, subject) is None
    assert db_session.get(Source, source.id) is not None
    html = text(signed_in.get(f"/subjects/{subject.id}/settings"))
    assert "data-syllabus-name></span>" in html
    assert f'<option value="{source.id}"' in html


def test_settings_same_board_keeps_syllabus(
    app, signed_in, subject, db_session, data, server
):
    source = upload(app, db_session, subject)
    use_as_syllabus(db_session, subject, source)
    assert settings(signed_in, subject, syllabus="").status_code == 302
    assert syllabus_of(db_session, subject).id == source.id


def test_settings_board_none_unmarks_and_ignores_choice(
    app, signed_in, subject, db_session, data, server
):
    source = upload(app, db_session, subject)
    use_as_syllabus(db_session, subject, source)
    assert settings(signed_in, subject, board="none").status_code == 302
    assert syllabus_of(db_session, subject) is None


def test_settings_shows_current_syllabus(
    app, signed_in, subject, db_session, data, server
):
    source = upload(app, db_session, subject, name="syllabus.txt")
    use_as_syllabus(db_session, subject, source)
    html = text(signed_in.get(f"/subjects/{subject.id}/settings"))
    assert "data-syllabus-name>syllabus.txt</span>" in html
    assert 'aria-label="Remove syllabus PDF"' in html
    assert "Change" in html


def test_new_subject_form_has_no_choose_from_sources(signed_in):
    html = text(signed_in.get("/subjects/new"))
    assert "Upload PDF" in html
    assert "syllabus-choose" not in html


def test_field_hidden_for_none(signed_in, db_session, teacher):
    plain = make_subject(db_session, teacher, board="none")
    html = text(signed_in.get(f"/subjects/{plain.id}/settings"))
    assert 'data-syllabus data-initial-board="none" hidden' in html
