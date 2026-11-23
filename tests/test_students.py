import io

import pytest
from sqlalchemy import select

from chalkboard.models import Student, Subject, Teacher
from chalkboard.passwords import hash_password
from chalkboard.students import CsvError, initials, parse_csv
from tests.conftest import csrf_token, sign_in


def text(response):
    return response.get_data(as_text=True)


def make_subject(db_session, teacher, name="Physics"):
    subject = Subject(
        teacher_id=teacher.id, name=name, class_minutes=60, colour="#0E7A64"
    )
    db_session.add(subject)
    db_session.commit()
    return subject


@pytest.fixture
def subject(db_session, teacher):
    return make_subject(db_session, teacher)


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def url(subject, tail=""):
    return f"/subjects/{subject.id}/students{tail}"


def post(client, subject, tail, **data):
    token = csrf_token(client, url(subject))
    return client.post(url(subject, tail), data={"csrf_token": token, **data})


def names(db_session, subject):
    rows = db_session.scalars(
        select(Student.name)
        .where(Student.subject_id == subject.id)
        .order_by(Student.id)
    )
    return list(rows)


def test_parse_csv_header_bom_and_notes():
    data = b'\xef\xbb\xbfname,note\r\nLeo Martins,"Needs large print, front row"\r\n\r\n  Mia   Chen \r\n'
    assert parse_csv(data) == [
        ("Leo Martins", "Needs large print, front row"),
        ("Mia Chen", ""),
    ]


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe\x00bad",
        b"",
        b"n" * 101 + b",x",
        b"Leo," + b"n" * 201,
        b"\n".join(b"Student %d" % i for i in range(2001)),
        b"x" * 1_000_001,
        b"name\n,\n",
    ],
)
def test_parse_csv_errors(data):
    with pytest.raises(CsvError):
        parse_csv(data)


def test_parse_csv_field_over_csv_limit():
    with pytest.raises(CsvError, match="as CSV"):
        parse_csv(b"Leo," + b"x" * 200_000)


def test_initials():
    assert initials("Leo Martins") == "LM"
    assert initials("Mia") == "M"
    assert initials("") == "?"
    assert initials("anna maria de souza") == "AS"


def test_empty_state(signed_in, subject):
    html = text(signed_in.get(url(subject)))
    assert "No students yet" in html
    assert "Names stay in Chalkboard." in html
    assert "Import CSV" in html
    assert "Add student" in html
    assert 'aria-current="page"' in html


def test_add_edit_delete(signed_in, subject, db_session):
    assert text(signed_in.get(url(subject, "/new"))).count("Add student") >= 2
    assert (
        post(signed_in, subject, "/new", name="Leo Martins", note="").status_code == 302
    )
    html = text(signed_in.get(url(subject)))
    assert "Leo Martins" in html
    assert 'aria-label="Edit Leo Martins"' in html
    student = db_session.scalar(select(Student))

    assert "Delete student" in text(signed_in.get(url(subject, f"/{student.id}")))
    post(signed_in, subject, f"/{student.id}", name="Leo Martins", note="Front row")
    assert "Front row" in text(signed_in.get(url(subject)))

    response = post(signed_in, subject, f"/{student.id}/delete")
    assert response.status_code == 302
    html = text(signed_in.get(url(subject)))
    assert "Deleted Leo Martins" in html
    assert "No students yet" in html


def test_validation_errors(signed_in, subject):
    response = post(signed_in, subject, "/new", name="  ", note="")
    assert response.status_code == 400
    assert "Enter a name" in text(response)


def test_duplicate_name_rejected(signed_in, subject, db_session):
    post(signed_in, subject, "/new", name="Mia Chen", note="")
    response = post(signed_in, subject, "/new", name="mia  chen", note="")
    assert response.status_code == 400
    assert "mia chen is already on the list" in text(response)
    assert names(db_session, subject) == ["Mia Chen"]


def test_edit_keeps_own_name(signed_in, subject, db_session):
    post(signed_in, subject, "/new", name="Mia Chen", note="")
    student = db_session.scalar(select(Student))
    response = post(signed_in, subject, f"/{student.id}", name="MIA CHEN", note="x")
    assert response.status_code == 302
    assert names(db_session, subject) == ["MIA CHEN"]


def upload(client, subject, data, name="class.csv"):
    token = csrf_token(client, url(subject))
    return client.post(
        url(subject, "/import"),
        data={"csrf_token": token, "file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def test_import_csv(signed_in, subject, db_session):
    post(signed_in, subject, "/new", name="Mia Chen", note="")
    assert text(signed_in.get(url(subject, "/import"))).count("Import CSV") >= 2
    response = upload(
        signed_in,
        subject,
        b"name,note\nLeo Martins,\nmia chen,\nNoah Becker,Joined\nnoah  becker,\n",
    )
    assert response.status_code == 302
    assert names(db_session, subject) == ["Mia Chen", "Leo Martins", "Noah Becker"]
    assert "Added 2 students, skipped 2 already on the list" in text(
        signed_in.get(url(subject))
    )


def test_import_single_student_message(signed_in, subject):
    upload(signed_in, subject, b"Leo Martins\n")
    assert "Added 1 student<" in text(signed_in.get(url(subject)))


def test_import_errors_save_nothing(signed_in, subject, db_session):
    response = upload(signed_in, subject, b"\xff\xfe\x00")
    assert response.status_code == 400
    assert "Save the file as CSV (UTF-8)" in text(response)
    token = csrf_token(signed_in, url(subject))
    response = signed_in.post(url(subject, "/import"), data={"csrf_token": token})
    assert response.status_code == 400
    assert "Choose a CSV file" in text(response)
    assert names(db_session, subject) == []


def test_oversized_upload_refused(signed_in, subject):
    assert upload(signed_in, subject, b"x" * (3 * 1024 * 1024)).status_code == 413


def test_other_teachers_subject_404(signed_in, db_session, subject):
    other = Teacher(
        email="bo@example.com", name="Bo", password_hash=hash_password("pw")
    )
    db_session.add(other)
    db_session.commit()
    theirs = make_subject(db_session, other, "Chemistry")
    student = Student(subject_id=theirs.id, name="Zed Zhou")
    db_session.add(student)
    db_session.commit()
    for tail in ("", "/new", "/import", f"/{student.id}"):
        assert signed_in.get(url(theirs, tail)).status_code == 404
    for tail in ("/new", "/import", f"/{student.id}", f"/{student.id}/delete"):
        token = csrf_token(signed_in, url(subject))
        response = signed_in.post(
            url(theirs, tail), data={"csrf_token": token, "name": "x"}
        )
        assert response.status_code == 404
    # A student of another subject is not reachable through one's own subject.
    assert signed_in.get(url(subject, f"/{student.id}")).status_code == 404
    assert post(signed_in, subject, f"/{student.id}/delete").status_code == 404
    assert names(db_session, theirs) == ["Zed Zhou"]


def test_delete_needs_csrf(signed_in, subject, db_session):
    student = Student(subject_id=subject.id, name="Leo Martins")
    db_session.add(student)
    db_session.commit()
    assert signed_in.post(url(subject, f"/{student.id}/delete")).status_code == 400
    assert names(db_session, subject) == ["Leo Martins"]


def test_count_label(signed_in, subject, db_session):
    post(signed_in, subject, "/new", name="Leo Martins", note="")
    assert "1 student<" in text(signed_in.get(url(subject)))
    post(signed_in, subject, "/new", name="Mia Chen", note="")
    assert "2 students<" in text(signed_in.get(url(subject)))


def test_deleting_subject_deletes_students(db_session, subject):
    db_session.add(Student(subject_id=subject.id, name="Leo Martins"))
    db_session.commit()
    db_session.delete(subject)
    db_session.commit()
    assert db_session.scalar(select(Student)) is None
