import re
from datetime import date, time

import pytest
from sqlalchemy import select

from chalkboard.models import AllowedSite, Subject, Teacher
from chalkboard.passwords import hash_password
from chalkboard.subjects import (
    board_line,
    greeting,
    next_colour,
    schedule_line,
    switcher_line,
)
from tests.conftest import csrf_token, sign_in

SITES_MESSAGE = "Tick at least one site, or choose Uploads only"


def text(response):
    return response.get_data(as_text=True)


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def site(db_session, teacher, domain="openstax.org"):
    row = AllowedSite(teacher_id=teacher.id, domain=domain)
    db_session.add(row)
    db_session.commit()
    return row


def other_teacher(db_session):
    other = Teacher(
        email="bo@example.com", name="Bo", password_hash=hash_password("pw")
    )
    db_session.add(other)
    db_session.commit()
    return other


def valid(**overrides):
    fields = {
        "name": "Chemistry",
        "grade": "Grade 11",
        "class_minutes": "50",
        "board": "cbse",
        "state_board": "",
        "days": ["0", "2"],
        "time_0": "10:00",
        "time_2": "10:00",
        "term_start": "2026-11-23",
        "term_end": "2027-02-26",
        "web_access": "uploads",
        "colour": "#0E7A64",
    }
    fields.update(overrides)
    return {k: v for k, v in fields.items() if v is not None}


def post_form(client, path="/subjects/new", **fields):
    token = csrf_token(client, "/subjects/new")
    return client.post(path, data={"csrf_token": token, **valid(**fields)})


def make(db_session, teacher, **fields):
    values = {
        "teacher_id": teacher.id,
        "name": "Physics",
        "class_minutes": 50,
        "colour": "#0E7A64",
        "grade": "Grade 11",
    }
    values.update(fields)
    subject = Subject(**values)
    db_session.add(subject)
    db_session.commit()
    return subject


def only_subject(db_session):
    return db_session.scalars(select(Subject)).one()


def test_helpers():
    assert board_line("cbse", None, "Grade 11") == "CBSE · Grade 11"
    assert board_line("state", "Kerala State Board", "") == "Kerala State Board"
    assert board_line("none", None, "Grade 9") == "Grade 9"
    assert board_line("none", None, "") == ""
    assert schedule_line({}, 50) == "No timetable yet · 50 min"
    assert schedule_line({0: time(10)}, 50) == "Mon 10:00 · 50 min"
    assert switcher_line("Grade 11", 60) == "Grade 11 · 60 min"
    assert switcher_line("", 60) == "60 min"
    assert [greeting(h) for h in (9, 12, 16, 17, 23)] == [
        "Good morning",
        "Good afternoon",
        "Good afternoon",
        "Good evening",
        "Good evening",
    ]
    assert next_colour([]) == "#0E7A64"
    assert next_colour(["#0E7A64", "#B4492B"]) == "#3A57C9"
    six = ["#0E7A64", "#3A57C9", "#B4492B", "#7A4FB5", "#A8326E", "#2F6F8F"]
    assert next_colour(six + ["#0E7A64"]) == "#3A57C9"


def test_empty_state(signed_in):
    html = text(signed_in.get("/subjects"))
    assert "Welcome, Aamina" in html
    assert "Start with your first subject" in html
    assert 'href="/subjects/new"' in html
    assert "No subjects yet" in html


def test_create_stores_all_fields(signed_in, teacher, db_session):
    allowed = site(db_session, teacher)
    response = post_form(
        signed_in,
        board="state",
        state_board="Maharashtra State Board",
        web_access="web",
        sites=[str(allowed.id)],
        colour="#B4492B",
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/subjects"
    subject = only_subject(db_session)
    assert (subject.name, subject.grade, subject.class_minutes) == (
        "Chemistry",
        "Grade 11",
        50,
    )
    assert subject.board == "state"
    assert subject.state_board == "Maharashtra State Board"
    assert (subject.term_start, subject.term_end) == (
        date(2026, 11, 23),
        date(2027, 2, 26),
    )
    assert [(c.weekday, c.start) for c in subject.class_times] == [
        (0, time(10)),
        (2, time(10)),
    ]
    assert subject.web_access == "web"
    assert subject.sites == [allowed]
    assert subject.colour == "#B4492B"


def test_card_lines(signed_in, teacher, db_session):
    post_form(signed_in)
    html = text(signed_in.get("/subjects"))
    assert "CBSE · Grade 11" in html
    assert "Mon, Wed 10:00 · 50 min" in html
    assert "Good " in html
    post_form(signed_in, name="Biology", days=None)
    html = text(signed_in.get("/subjects"))
    biology = db_session.scalar(select(Subject).where(Subject.name == "Biology"))
    assert "No timetable yet · 50 min" in html
    assert f'href="/subjects/{biology.id}/settings">Add class times</a>' in html


def test_web_without_site_refused(signed_in, teacher, db_session):
    site(db_session, teacher)
    response = post_form(signed_in, web_access="web")
    assert response.status_code == 400
    assert SITES_MESSAGE in text(response)
    assert db_session.scalars(select(Subject)).all() == []


def test_open_mode_needs_no_site(signed_in, teacher, db_session):
    teacher.allow_any_site = True
    db_session.commit()
    assert "Any website is allowed" in text(signed_in.get("/subjects/new"))
    assert post_form(signed_in, web_access="web").status_code == 302
    assert only_subject(db_session).web_access == "web"


def test_unknown_day_refused_not_500(signed_in, db_session):
    response = post_form(signed_in, days=["6"])
    assert response.status_code == 400
    assert "is not a valid choice" in text(response)
    # Times for the valid days are still checked.
    response = post_form(signed_in, days=["0", "6"], time_0="")
    assert response.status_code == 400
    assert "Enter a start time for Mon" in text(response)
    assert db_session.scalar(select(Subject)) is None


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"board": "state"}, "Enter the state board"),
        ({"time_0": ""}, "Enter a start time for Mon"),
        ({"term_end": ""}, "Enter both the first and last day of the term"),
        ({"term_start": ""}, "Enter both the first and last day of the term"),
        (
            {"term_start": "2027-03-01"},
            "The last day must be on or after the first day",
        ),
        ({"name": "  "}, "Enter a name"),
        ({"class_minutes": "0"}, "Enter a class length between 10 and 240 minutes"),
        ({"class_minutes": "241"}, "Enter a class length between 10 and 240 minutes"),
        ({"class_minutes": "abc"}, "Enter a class length between 10 and 240 minutes"),
        ({"board": "gcse"}, "Not a valid choice"),
        ({"web_access": "maybe"}, "Invalid value"),
        ({"colour": "#000000"}, "Not a valid choice"),
    ],
)
def test_invalid_fields_refused(signed_in, db_session, fields, message):
    response = post_form(signed_in, **fields)
    assert response.status_code == 400
    assert message in text(response)
    assert db_session.scalars(select(Subject)).all() == []


def test_class_length_bounds_accepted(signed_in, db_session):
    assert post_form(signed_in, class_minutes="10").status_code == 302
    assert post_form(signed_in, name="Maths", class_minutes="240").status_code == 302


def test_edit_prefills_and_updates(signed_in, teacher, db_session):
    allowed = site(db_session, teacher)
    post_form(
        signed_in,
        board="state",
        state_board="Maharashtra State Board",
        web_access="web",
        sites=[str(allowed.id)],
    )
    subject = only_subject(db_session)
    html = text(signed_in.get(f"/subjects/{subject.id}/settings"))
    assert "Subject settings" in html
    assert 'value="Chemistry"' in html
    assert "28 classes · 23 h 20 min" in html
    assert "Save changes" in html
    response = post_form(
        signed_in,
        f"/subjects/{subject.id}/settings",
        board="cbse",
        days=["0"],
        time_2="",
        web_access="uploads",
    )
    assert response.status_code == 302
    db_session.refresh(subject)
    assert [c.weekday for c in subject.class_times] == [0]
    assert subject.sites == []
    assert subject.board == "cbse"
    assert subject.state_board is None


def test_edit_keeps_ticked_sites_in_open_mode(signed_in, teacher, db_session):
    allowed = site(db_session, teacher)
    subject = make(db_session, teacher, web_access="web", sites=[allowed])
    teacher.allow_any_site = True
    db_session.commit()
    response = post_form(
        signed_in, f"/subjects/{subject.id}/settings", web_access="web"
    )
    assert response.status_code == 302
    db_session.refresh(subject)
    assert subject.sites == [allowed]


def test_invalid_edit_rerenders_settings(signed_in, teacher, db_session):
    subject = make(db_session, teacher)
    response = post_form(signed_in, f"/subjects/{subject.id}/settings", name="")
    assert response.status_code == 400
    assert "Subject settings" in text(response)


def test_other_teachers_subject_404(signed_in, db_session):
    subject = make(db_session, other_teacher(db_session), name="Secret")
    base = f"/subjects/{subject.id}"
    assert signed_in.get(base).status_code == 404
    assert signed_in.get(f"{base}/settings").status_code == 404
    assert post_form(signed_in, f"{base}/settings").status_code == 404
    assert "Secret" not in text(signed_in.get("/subjects"))


def test_foreign_site_id_refused(signed_in, db_session):
    foreign = site(db_session, other_teacher(db_session))
    response = post_form(signed_in, web_access="web", sites=[str(foreign.id)])
    assert response.status_code == 400
    assert db_session.scalars(select(Subject)).all() == []


def test_colour_auto_and_fixed_set(signed_in, db_session):
    def checked(html):
        return re.search(r'name="colour" value="(#\w+)" checked', html).group(1)

    assert checked(text(signed_in.get("/subjects/new"))) == "#0E7A64"
    post_form(signed_in)
    assert checked(text(signed_in.get("/subjects/new"))) == "#3A57C9"
    assert post_form(signed_in, name="X", colour="#000000").status_code == 400


def test_switcher_lists_subjects(signed_in, teacher, db_session):
    first = make(db_session, teacher, name="Physics")
    second = make(db_session, teacher, name="Maths")
    html = text(signed_in.get("/subjects"))
    assert "2 subjects" in html
    for subject in (first, second):
        assert re.search(
            rf'href="/subjects/{subject.id}" aria-label="{subject.name}"', html
        )
    assert 'aria-label="New subject"' in html


def test_open_subject_shows_pages(signed_in, teacher, db_session):
    subject = make(db_session, teacher, class_minutes=50)
    response = signed_in.get(f"/subjects/{subject.id}")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/subjects/{subject.id}/today")
    response = signed_in.get(response.headers["Location"])
    assert response.status_code == 200
    html = text(response)
    nav = html[html.index('<nav id="sidebar-nav"') : html.index("</nav>")]
    for label in ("Today", "Course overview", "Chapters", "Sources", "Students"):
        assert label in nav
    assert "Grade 11 · 50 min" in html
    assert 'aria-label="Subject settings"' in html
    db_session.refresh(teacher)
    assert teacher.last_subject_id == subject.id


def test_requires_csrf(signed_in, db_session):
    response = signed_in.post("/subjects/new", data=valid())
    assert response.status_code == 400
    assert db_session.scalars(select(Subject)).all() == []
