import pytest
from sqlalchemy import func, select

from chalkboard.models import AllowedSite, Teacher
from chalkboard.passwords import hash_password
from chalkboard.settings_page import SiteForm
from tests.conftest import csrf_token, sign_in


def text(response):
    return response.get_data(as_text=True)


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def add(client, domain):
    return client.post(
        "/settings/sites",
        data={"domain": domain, "csrf_token": csrf_token(client, "/settings")},
    )


def count(db_session):
    return db_session.scalar(select(func.count()).select_from(AllowedSite))


def other_teacher(db_session):
    other = Teacher(
        email="bo@example.com", name="Bo", password_hash=hash_password("pw")
    )
    db_session.add(other)
    db_session.commit()
    return other


def test_add_site_normalises_and_lists(signed_in, db_session):
    assert add(signed_in, "https://www.OpenStax.org/books").status_code == 302
    html = text(signed_in.get("/settings"))
    assert "openstax.org" in html
    assert "Not used yet" in html
    assert db_session.scalar(select(AllowedSite.domain)) == "openstax.org"


def test_add_duplicate_rejected(signed_in, db_session):
    add(signed_in, "openstax.org")
    response = add(signed_in, "OpenStax.ORG.")
    assert response.status_code == 400
    assert "openstax.org is already on the list" in text(response)
    assert count(db_session) == 1


def test_add_duplicate_race_returns_400(signed_in, db_session, monkeypatch):
    add(signed_in, "openstax.org")
    # Skip the pre-insert duplicate check, as a parallel request would.
    monkeypatch.setattr(SiteForm, "validate_domain", lambda self, field: None)
    response = add(signed_in, "openstax.org")
    assert response.status_code == 400
    assert "openstax.org is already on the list" in text(response)
    assert count(db_session) == 1


@pytest.mark.parametrize("value", ["localhost", "10.0.0.1", ""])
def test_add_invalid_rejected(signed_in, db_session, value):
    response = add(signed_in, value)
    assert response.status_code == 400
    assert 'class="field-error"' in text(response)
    assert count(db_session) == 0


def test_remove_site(signed_in, teacher, db_session):
    add(signed_in, "openstax.org")
    site_id = db_session.scalar(select(AllowedSite.id))
    response = signed_in.post(
        f"/settings/sites/{site_id}/delete",
        data={"csrf_token": csrf_token(signed_in, "/settings")},
    )
    assert response.status_code == 302
    assert count(db_session) == 0


def test_remove_other_teachers_site_404(signed_in, db_session):
    site = AllowedSite(teacher_id=other_teacher(db_session).id, domain="ck12.org")
    db_session.add(site)
    db_session.commit()
    response = signed_in.post(
        f"/settings/sites/{site.id}/delete",
        data={"csrf_token": csrf_token(signed_in, "/settings")},
    )
    assert response.status_code == 404
    assert count(db_session) == 1


def test_sites_are_per_teacher(signed_in, db_session):
    db_session.add(
        AllowedSite(teacher_id=other_teacher(db_session).id, domain="ck12.org")
    )
    db_session.commit()
    assert "ck12.org" not in text(signed_in.get("/settings"))


def test_open_mode_off_by_default_and_persists(signed_in, teacher, db_session):
    assert teacher.allow_any_site is False
    assert 'aria-checked="false"' in text(signed_in.get("/settings"))

    def post(value):
        return signed_in.post(
            "/settings/sites/open",
            data={
                "allow_any": value,
                "csrf_token": csrf_token(signed_in, "/settings"),
            },
        )

    assert post("1").status_code == 302
    db_session.refresh(teacher)
    assert teacher.allow_any_site is True
    assert 'aria-checked="true"' in text(signed_in.get("/settings"))
    post("0")
    db_session.refresh(teacher)
    assert teacher.allow_any_site is False
    assert post("maybe").status_code == 400


def test_site_posts_need_csrf(signed_in, db_session):
    add(signed_in, "openstax.org")
    site_id = db_session.scalar(select(AllowedSite.id))
    for path in (
        "/settings/sites",
        f"/settings/sites/{site_id}/delete",
        "/settings/sites/open",
    ):
        assert (
            signed_in.post(path, data={"domain": "x.org", "allow_any": "1"}).status_code
            == 400
        )
    assert count(db_session) == 1
