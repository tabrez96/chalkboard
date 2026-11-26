import pytest

from tests.conftest import csrf_token, sign_in


@pytest.fixture
def signed_in(client, teacher):
    sign_in(client)
    return client


def post(client, **data):
    return client.post(
        "/settings/web-search",
        data={"csrf_token": csrf_token(client, "/settings"), **data},
    )


def card(client):
    html = client.get("/settings").get_data(as_text=True)
    assert "Assistant may search the web" in html
    return html[html.index('aria-labelledby="web-title"') :]


def test_web_search_off_by_default_and_toggles(signed_in, teacher, db_session):
    assert teacher.web_search_enabled is False
    assert 'aria-checked="false"' in card(signed_in)
    response = post(signed_in, enabled="1")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings#web-title")
    db_session.refresh(teacher)
    assert teacher.web_search_enabled is True
    assert 'aria-checked="true"' in card(signed_in)
    post(signed_in, enabled="0")
    db_session.refresh(teacher)
    assert teacher.web_search_enabled is False


def test_web_search_rejects_bad_posts(signed_in, teacher, db_session):
    assert post(signed_in, enabled="2").status_code == 400
    no_token = signed_in.post("/settings/web-search", data={"enabled": "1"})
    assert no_token.status_code == 400
    db_session.refresh(teacher)
    assert teacher.web_search_enabled is False
