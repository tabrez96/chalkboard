import io
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from chalkboard import jobs, sources
from chalkboard.ai import FACTORY_KEY, RedactingClient
from chalkboard.ai.fake import FakeOllama
from chalkboard.models import (
    AllowedSite,
    Chunk,
    Source,
    Subject,
    SyllabusUnit,
    Teacher,
)
from chalkboard.passwords import hash_password
from chalkboard.sources_page import pages_label, sites_line, status_tone, summary_line
from tests.conftest import csrf_token, sign_in
from tests.pdfs import make_pdf
from tests.test_sources import TEXT, docx_bytes

LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)
UNSUPPORTED = "Upload a PDF, Word (.docx), text or Markdown file"


@pytest.fixture
def subject(db_session, teacher):
    row = Subject(
        teacher_id=teacher.id, name="Physics", class_minutes=60, colour="#0E7A64"
    )
    db_session.add(row)
    db_session.commit()
    return row


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


def url(subject, tail=""):
    return f"/subjects/{subject.id}/sources{tail}"


def text(response):
    return response.get_data(as_text=True)


def post_files(client, subject, *files):
    token = csrf_token(client, url(subject))
    return client.post(
        url(subject, "/upload"),
        data={"csrf_token": token, "file": [(io.BytesIO(b), n) for n, b in files]},
        content_type="multipart/form-data",
    )


def count(db_session):
    return db_session.scalar(select(func.count()).select_from(Source))


def test_sidebar_links_to_sources(signed_in, subject):
    html = text(signed_in.get(f"/subjects/{subject.id}/students"))
    assert f'href="/subjects/{subject.id}/sources"' in html


def test_empty_state(signed_in, subject):
    html = text(signed_in.get(url(subject)))
    assert "No sources yet" in html
    assert "Add the textbooks, notes and pages you trust." in html
    assert "<table" not in html
    assert "Upload files" in html


@pytest.mark.parametrize("kind", ["pdf", "docx", "txt", "md"])
def test_upload_each_kind_ends_ready(app, signed_in, subject, data, server, kind):
    content = {
        "pdf": make_pdf(["Alpha one two", "Beta three four"]),
        "docx": docx_bytes(),
        "txt": TEXT,
        "md": TEXT,
    }[kind]
    response = post_files(signed_in, subject, (f"f.{kind}", content))
    assert response.status_code == 303
    assert response.headers["Location"] == url(subject)
    jobs.run_once(app)
    html = text(signed_in.get(url(subject)))
    assert 'source-num">1<' in html
    assert re.search(r"Ready · \d+ passage", html)
    assert "Uploaded" in html


def test_upload_several_files_numbers_them(app, signed_in, subject, data, server):
    post_files(signed_in, subject, ("a.txt", TEXT), ("b.txt", TEXT))
    html = text(signed_in.get(url(subject)))
    assert 'source-num">1<' in html
    assert 'source-num">2<' in html
    assert "2 sources" in html


def test_status_chip_polls_while_reading_then_stops(
    app, signed_in, subject, data, server
):
    post_files(signed_in, subject, ("a.txt", TEXT))
    assert f'hx-get="{url(subject, "/live")}"' in text(signed_in.get(url(subject)))
    assert "Reading · 0%" in text(signed_in.get(url(subject, "/live")))
    jobs.run_once(app)
    html = text(signed_in.get(url(subject, "/live")))
    assert "hx-trigger" not in html
    assert "Ready · " in html


def test_ollama_down_shows_waiting_and_banner(
    app, signed_in, subject, data, server, db_session
):
    server.up = False
    post_files(signed_in, subject, ("a.txt", TEXT))
    jobs.run_once(app)
    html = text(signed_in.get(url(subject)))
    assert "Saved · waiting for Ollama" in html
    assert "Can&#39;t reach the Ollama server" in html or (
        "Can't reach the Ollama server" in html
    )
    server.up = True
    source = db_session.scalar(select(Source))
    db_session.refresh(source.job)
    source.job.run_after = LONG_AGO
    db_session.commit()
    jobs.run_once(app)
    assert "Ready · " in text(signed_in.get(url(subject)))


def test_unsupported_type_refused_saves_nothing(signed_in, subject, data, db_session):
    response = post_files(signed_in, subject, ("a.txt", TEXT), ("virus.exe", b"MZ"))
    assert response.status_code == 303
    assert f"virus.exe: {UNSUPPORTED}" in text(signed_in.get(url(subject)))
    assert count(db_session) == 0
    assert not list(data.rglob("*.txt"))


def test_oversized_file_refused(signed_in, subject, data, db_session, monkeypatch):
    monkeypatch.setattr(sources, "MAX_UPLOAD_BYTES", 10)
    post_files(signed_in, subject, ("big.txt", b"x" * 11))
    assert "The file is larger than 50 MB" in text(signed_in.get(url(subject)))
    assert count(db_session) == 0


def test_request_over_limit_redirects_with_message(
    app, signed_in, subject, data, db_session
):
    token = csrf_token(signed_in, url(subject))
    app.config["MAX_CONTENT_LENGTH"] = 1000
    response = signed_in.post(
        url(subject, "/upload"),
        data={"csrf_token": token, "file": (io.BytesIO(b"x" * 2000), "big.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 303
    assert response.headers["Location"] == url(subject)
    assert "The file is larger than 50 MB" in text(signed_in.get(url(subject)))
    assert count(db_session) == 0


def test_no_file_chosen(signed_in, subject):
    token = csrf_token(signed_in, url(subject))
    signed_in.post(url(subject, "/upload"), data={"csrf_token": token})
    assert "Choose a file to upload" in text(signed_in.get(url(subject)))


def test_upload_without_csrf_is_400(signed_in, subject, db_session):
    response = signed_in.post(
        url(subject, "/upload"),
        data={"file": (io.BytesIO(TEXT), "a.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert count(db_session) == 0


def uploaded(client, subject, db_session, name="notes.txt"):
    post_files(client, subject, (name, TEXT))
    return db_session.scalar(select(Source))


def test_delete_needs_confirmation(app, signed_in, subject, data, server, db_session):
    source = uploaded(signed_in, subject, db_session)
    jobs.run_once(app)
    sid, path = source.id, data / source.file_path
    html = text(signed_in.get(url(subject, f"/{sid}/delete")))
    assert "Delete source 1?" in html
    assert "hx-trigger" not in html
    assert db_session.get(Source, sid) is not None and path.exists()
    token = csrf_token(signed_in, url(subject))
    response = signed_in.post(
        url(subject, f"/{sid}/delete"), data={"csrf_token": token}
    )
    assert response.status_code == 303
    db_session.expire_all()
    assert db_session.get(Source, sid) is None
    assert not path.exists()
    assert db_session.scalar(select(func.count()).select_from(Chunk)) == 0
    assert "Deleted notes.txt" in text(signed_in.get(url(subject)))


def test_delete_confirmation_lists_affected_plans(
    signed_in, subject, data, db_session, monkeypatch
):
    source = uploaded(signed_in, subject, db_session)
    monkeypatch.setattr(
        "chalkboard.sources_page.affected_plans", lambda s, src: ["Chapter 2 · Motion"]
    )
    html = text(signed_in.get(url(subject, f"/{source.id}/delete")))
    assert "Chapter 2 · Motion" in html
    assert "source deleted" in html


def test_delete_without_csrf_is_400(signed_in, subject, data, db_session):
    source = uploaded(signed_in, subject, db_session)
    sid = source.id
    assert signed_in.post(url(subject, f"/{sid}/delete")).status_code == 400
    assert db_session.get(Source, sid) is not None


def test_trash_aria_label(signed_in, subject, data, db_session):
    uploaded(signed_in, subject, db_session)
    assert 'aria-label="Delete source 1"' in text(signed_in.get(url(subject)))


def test_name_opens_extracted_text(app, signed_in, subject, data, server, db_session):
    source = uploaded(signed_in, subject, db_session, name="two.pdf")
    # Replace with a real two-page PDF, then ingest.
    (data / source.file_path).write_bytes(
        make_pdf(["Alpha one two", "Beta three four"])
    )
    source.kind = "pdf"
    db_session.commit()
    jobs.run_once(app)
    assert f'href="{url(subject, f"/{source.id}")}"' in text(
        signed_in.get(url(subject))
    )
    html = text(signed_in.get(url(subject, f"/{source.id}")))
    assert "Passage 1" in html
    assert "Pages 1–2" in html
    assert html.index("Alpha one two") < html.index("Beta three four")


def test_sites_line_variants(signed_in, subject, db_session, teacher):
    def page():
        return text(signed_in.get(url(subject)))

    html = page()
    assert "Uploads only · " in html
    assert f'href="/subjects/{subject.id}/settings"' in html
    db_session.add_all(
        [
            AllowedSite(teacher_id=teacher.id, domain=d)
            for d in ("openstax.org", "khanacademy.org")
        ]
    )
    db_session.flush()
    subject.web_access = "web"
    subject.sites = list(db_session.scalars(select(AllowedSite)))
    db_session.commit()
    html = page()
    assert "Pages from khanacademy.org, openstax.org" in html
    assert f'href="/subjects/{subject.id}/settings"' in html
    teacher.allow_any_site = True
    db_session.commit()
    html = page()
    assert "Any website is allowed" in html
    assert re.search(r'<a href="/settings">Change</a>', html)


def test_other_teachers_source_404(signed_in, subject, data, db_session):
    other = Teacher(
        email="bo@example.com", name="Bo", password_hash=hash_password("pw")
    )
    db_session.add(other)
    db_session.commit()
    theirs = Subject(
        teacher_id=other.id, name="Chemistry", class_minutes=45, colour="#3A57C9"
    )
    db_session.add(theirs)
    db_session.commit()
    source = Source(
        subject_id=theirs.id,
        number=1,
        name="x.txt",
        kind="txt",
        file_path="sources/x.txt",
    )
    db_session.add(source)
    db_session.commit()
    for tail in ("", f"/{source.id}", f"/{source.id}/delete"):
        assert signed_in.get(f"/subjects/{theirs.id}/sources{tail}").status_code == 404
    # Own subject, someone else's source id
    assert signed_in.get(url(subject, f"/{source.id}")).status_code == 404
    assert signed_in.get(url(subject, f"/{source.id}/delete")).status_code == 404
    token = csrf_token(signed_in, url(subject))
    response = signed_in.post(
        url(subject, f"/{source.id}/delete"), data={"csrf_token": token}
    )
    assert response.status_code == 404
    assert db_session.get(Source, source.id) is not None


def test_sites_line():
    assert sites_line("uploads", ["a.org"], False) == (
        "Uploads only",
        "subjects.settings",
    )
    assert sites_line("web", [], False) == ("Uploads only", "subjects.settings")
    assert sites_line("uploads", [], True) == ("Uploads only", "subjects.settings")
    assert sites_line("web", ["a.org", "b.org"], False) == (
        "Pages from a.org, b.org",
        "subjects.settings",
    )
    assert sites_line("web", ["a.org"], True) == (
        "Any website is allowed",
        "settings.show",
    )


def test_summary_line():
    assert summary_line(1, 1) == "1 source · 1 passage"
    assert summary_line(4, 91) == "4 sources · 91 passages"
    assert summary_line(2, 0) == "2 sources · 0 passages"


def test_pages_label():
    assert pages_label(None, None) == ""
    assert pages_label(3, 3) == "Page 3"
    assert pages_label(3, 4) == "Pages 3–4"


@pytest.mark.parametrize(
    ("state", "tone"),
    [
        (None, "chip-ok"),
        ("done", "chip-ok"),
        ("queued", "chip-ok"),
        ("running", "chip-ok"),
        ("waiting_ollama", "chip-flag"),
        ("failed", "chip-danger"),
    ],
)
def test_status_tone(state, tone):
    job = None if state is None else jobs.Job(state=state)
    assert status_tone(Source(job=job)) == tone


def test_summary_line_with_syllabus():
    assert summary_line(4, 91, syllabus=True) == "Syllabus + 4 sources · 91 passages"


@pytest.fixture
def cbse(subject, db_session):
    subject.board = "cbse"
    subject.grade = "Class 11"
    db_session.commit()
    return subject


def add_source(db_session, subject, number, **fields):
    source = Source(
        subject_id=subject.id,
        number=number,
        name=f"Notes {number}.txt",
        kind="txt",
        file_path=f"sources/{number}.txt",
        **fields,
    )
    db_session.add(source)
    db_session.commit()
    return source


def post(client, subject, tail, **form):
    token = csrf_token(client, url(subject))
    return client.post(url(subject, tail), data={"csrf_token": token, **form})


def add_units(db_session, source, titles, typed=False):
    db_session.add_all(
        SyllabusUnit(
            source_id=source.id, position=i, number=i + 1, title=t, typed=typed
        )
        for i, t in enumerate(titles)
    )
    db_session.commit()


def test_use_as_syllabus_without_existing_marks_at_once(
    signed_in, cbse, db_session, data
):
    source = add_source(db_session, cbse, 1)
    response = post(signed_in, cbse, f"/{source.id}/syllabus")
    assert response.status_code == 303
    html = text(signed_in.get(url(cbse)))
    assert 'class="syllabus-row"' in html
    assert ">Syllabus<" in html
    assert "CBSE · Class 11" in html
    assert "Notes 1.txt" in html
    assert 'source-num">1<' not in html
    assert "Syllabus + 0 sources · 0 passages" in html


def test_bookmark_aria_label(signed_in, cbse, db_session):
    add_source(db_session, cbse, 1)
    html = text(signed_in.get(url(cbse)))
    assert 'aria-label="Use source 1 as syllabus"' in html


def test_replace_asks_first(signed_in, cbse, db_session):
    first = add_source(db_session, cbse, 1, is_syllabus=True)
    second = add_source(db_session, cbse, 2)
    html = text(signed_in.get(url(cbse, f"/{second.id}/syllabus")))
    assert "Use this as the syllabus?" in html
    assert "<em>Notes 1.txt</em>" in html
    assert post(signed_in, cbse, f"/{second.id}/syllabus").status_code == 303
    html = text(signed_in.get(url(cbse)))
    assert 'source-num">1<' in html
    db_session.refresh(first)
    assert (first.is_syllabus, first.number) == (False, 1)


def test_confirm_redirects_when_nothing_to_replace(signed_in, cbse, db_session):
    source = add_source(db_session, cbse, 1)
    assert signed_in.get(url(cbse, f"/{source.id}/syllabus")).status_code == 303


def test_syllabus_row_status_and_see_units(signed_in, cbse, db_session):
    source = add_source(db_session, cbse, 1, is_syllabus=True)
    add_units(db_session, source, ["Motion", "Forces", "Energy", "Waves", "Heat"])
    html = text(signed_in.get(url(cbse)))
    assert "Ready · 5 units" in html
    assert "See units" in html
    html = text(signed_in.get(url(cbse, f"/{source.id}/units")))
    assert "<ol" in html
    assert html.index("Motion") < html.index("Forces") < html.index("Heat")


def test_type_the_units_when_none_found(signed_in, cbse, db_session):
    source = add_source(db_session, cbse, 1, is_syllabus=True)
    html = text(signed_in.get(url(cbse)))
    assert "No units found" in html
    assert "Type the units" in html
    assert "autofocus" in text(signed_in.get(url(cbse, f"/{source.id}/units")))
    assert post(signed_in, cbse, f"/{source.id}/units", units="A\nB").status_code == 303
    html = text(signed_in.get(url(cbse, f"/{source.id}/units")))
    assert html.index("</span> A</li>") < html.index("</span> B</li>")
    assert "Typed by you" in html
    assert "Ready · 2 units" in text(signed_in.get(url(cbse)))


def test_type_units_blank_is_422(signed_in, cbse, db_session):
    source = add_source(db_session, cbse, 1, is_syllabus=True)
    response = post(signed_in, cbse, f"/{source.id}/units", units="  \n ")
    assert response.status_code == 422
    assert "Type at least one unit" in text(response)
    assert "autofocus" in text(response)


def test_syllabus_delete_label_and_title(signed_in, cbse, db_session):
    source = add_source(db_session, cbse, 1, is_syllabus=True)
    assert 'aria-label="Delete the syllabus"' in text(signed_in.get(url(cbse)))
    html = text(signed_in.get(url(cbse, f"/{source.id}/delete")))
    assert "Delete the syllabus?" in html
    assert "Delete syllabus" in html


def test_board_none_hides_syllabus(signed_in, subject, db_session):
    source = add_source(db_session, subject, 1, is_syllabus=True)
    html = text(signed_in.get(url(subject)))
    assert "syllabus-row" not in html
    assert "as syllabus" not in html
    assert 'source-num">1<' in html
    assert post(signed_in, subject, f"/{source.id}/syllabus").status_code == 400
    assert signed_in.get(url(subject, f"/{source.id}/units")).status_code == 404


def test_use_as_syllabus_without_csrf_is_400(signed_in, cbse, db_session):
    source = add_source(db_session, cbse, 1)
    response = signed_in.post(url(cbse, f"/{source.id}/syllabus"))
    assert response.status_code == 400
    db_session.refresh(source)
    assert not source.is_syllabus


def test_other_subjects_source_is_404(signed_in, cbse, db_session):
    other = Teacher(
        email="bo@example.com", name="Bo", password_hash=hash_password("pw")
    )
    db_session.add(other)
    db_session.commit()
    theirs = Subject(
        teacher_id=other.id, name="Chemistry", class_minutes=45, colour="#3A57C9"
    )
    db_session.add(theirs)
    db_session.commit()
    source = add_source(db_session, theirs, 1, is_syllabus=True)
    assert signed_in.get(url(cbse, f"/{source.id}/syllabus")).status_code == 404
    assert signed_in.get(url(cbse, f"/{source.id}/units")).status_code == 404
