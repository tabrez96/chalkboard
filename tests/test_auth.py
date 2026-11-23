import pytest
from flask import Blueprint
from sqlalchemy import delete

from chalkboard.models import Subject, Teacher
from chalkboard.passwords import hash_password
from tests.conftest import csrf_token, make_app, sign_in


def assert_error_state(response, email):
    assert response.status_code == 401
    html = response.get_data(as_text=True)
    assert 'role="alert"' in html
    assert "That email and password don" in html
    assert 'aria-invalid="true"' in html
    assert "autofocus" in html.split('id="password"')[1].split(">")[0]
    assert f'value="{email}"' in html


def signed_in(client, teacher):
    assert sign_in(client).status_code == 302


def test_login_page_renders(client):
    response = client.get("/login")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    for text in (
        "Welcome back",
        "Sign in",
        "flask reset-password",
        'name="csrf_token"',
    ):
        assert text in html
    assert 'autocomplete="current-password"' in html
    assert 'role="alert"' not in html


def test_correct_credentials_go_to_subjects(client, teacher):
    response = sign_in(client)
    assert response.status_code == 302
    assert response.headers["Location"] == "/subjects"
    page = client.get("/subjects")
    assert page.status_code == 200
    assert "aamina@example.com" in page.get_data(as_text=True)


def test_root_redirects_home(client, teacher):
    signed_in(client, teacher)
    assert client.get("/").headers["Location"] == "/subjects"


def test_email_case_insensitive(client, teacher):
    assert sign_in(client, email=" AAMINA@example.com ").status_code == 302


def test_whitespace_password_signs_in(client, teacher, db_session):
    teacher.password_hash = hash_password("        ")
    db_session.commit()
    assert sign_in(client, password="        ").status_code == 302


def test_wrong_password_shows_error_state(client, teacher):
    response = sign_in(client, password="nope")
    assert_error_state(response, "aamina@example.com")
    with client.session_transaction() as session:
        assert "teacher_id" not in session


def test_unknown_email_shows_error_state(client, teacher):
    assert_error_state(sign_in(client, email="who@example.com"), "who@example.com")


def test_empty_fields_show_error_state(client, teacher):
    response = sign_in(client, email="", password="")
    assert response.status_code == 401
    assert 'role="alert"' in response.get_data(as_text=True)


def test_post_without_csrf_rejected(client, teacher):
    response = client.post(
        "/login", data={"email": "aamina@example.com", "password": "correct horse"}
    )
    assert response.status_code == 400
    with client.session_transaction() as session:
        assert "teacher_id" not in session


def test_logout_without_csrf_rejected(client, teacher):
    signed_in(client, teacher)
    assert client.post("/logout").status_code == 400
    assert client.get("/subjects").status_code == 200


def test_logout(client, teacher):
    signed_in(client, teacher)
    token = csrf_token(client, "/subjects")
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert client.get("/subjects").headers["Location"].startswith("/login")


def test_every_page_requires_sign_in(app, client):
    skipped = {"static", "auth.login", "health.healthz"}
    checked = 0
    for rule in app.url_map.iter_rules():
        if rule.endpoint in skipped or "GET" not in rule.methods:
            continue
        url = app.url_map.bind("localhost").build(
            rule.endpoint, {a: 1 for a in rule.arguments}
        )
        response = client.get(url)
        assert response.status_code == 302, url
        assert response.headers["Location"].startswith("/login"), url
        checked += 1
    assert checked >= 2


def test_public_pages_open(client):
    assert client.get("/login").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_unknown_url_is_404_signed_out(client):
    assert client.get("/no-such-page").status_code == 404


def test_post_to_protected_url_signed_out_has_no_next(client):
    token = csrf_token(client)
    response = client.post("/logout", data={"csrf_token": token})
    assert response.headers["Location"] == "/login"


def test_next_is_followed_when_safe(client, teacher):
    location = client.get("/subjects").headers["Location"]
    assert location == "/login?next=/subjects"
    response = sign_in(client, next="/subjects")
    assert response.headers["Location"] == "/subjects"


@pytest.mark.parametrize(
    "target", ["https://evil.example", "//evil.example", "/\\evil.example"]
)
def test_unsafe_next_ignored(client, teacher, target):
    assert sign_in(client, next=target).headers["Location"] == "/subjects"


def last_subject(db_session, teacher) -> int:
    subject = Subject(
        teacher_id=teacher.id, name="Physics", class_minutes=50, colour="#0E7A64"
    )
    db_session.add(subject)
    db_session.flush()
    teacher.last_subject_id = subject.id
    db_session.commit()
    return subject.id


def test_last_subject_goes_to_today(db_connection, db_session, teacher):
    app = make_app(db_connection)
    today = Blueprint("today", __name__)

    @today.get("/subjects/<int:subject_id>/today")
    def show(subject_id):
        return "today"

    app.register_blueprint(today)
    subject_id = last_subject(db_session, teacher)
    client = app.test_client()
    assert sign_in(client).headers["Location"] == f"/subjects/{subject_id}/today"


def test_last_subject_without_today_page_goes_to_subjects(client, db_session, teacher):
    last_subject(db_session, teacher)
    assert sign_in(client).headers["Location"] == "/subjects"


def test_deleted_teacher_session_cleared(client, db_session, teacher):
    signed_in(client, teacher)
    db_session.execute(delete(Teacher))
    db_session.commit()
    assert client.get("/subjects").headers["Location"].startswith("/login")
    with client.session_transaction() as session:
        assert "teacher_id" not in session


def test_signed_in_login_redirects_home(client, teacher):
    signed_in(client, teacher)
    response = client.get("/login")
    assert response.status_code == 302
    assert response.headers["Location"] == "/subjects"


def test_old_hash_is_upgraded_on_sign_in(client, db_session, teacher):
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
        "correct horse"
    )
    teacher.password_hash = weak
    db_session.commit()
    assert sign_in(client).status_code == 302
    db_session.refresh(teacher)
    assert teacher.password_hash != weak
