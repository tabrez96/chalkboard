import re
from types import SimpleNamespace

from flask import g, render_template

from chalkboard.models import Teacher
from chalkboard.passwords import hash_password
from tests.conftest import csrf_token, sign_in


def signed_in(client, teacher):
    sign_in(client)
    return client


def page(client, path):
    return client.get(path).get_data(as_text=True)


def post(client, path, **data):
    token = csrf_token(client, "/settings")
    return client.post(path, data={"csrf_token": token, **data})


def test_new_account_defaults(client, teacher):
    signed_in(client, teacher)
    html = page(client, "/subjects")
    assert 'data-theme="system"' in html
    assert 'aria-expanded="true"' in html
    assert "Collapse sidebar" in html
    assert "is-collapsed" not in html


def test_no_subject_shows_choose_and_hides_pages(client, teacher):
    signed_in(client, teacher)
    html = page(client, "/subjects")
    assert "Choose a subject" in html
    nav = html[html.index('<nav id="sidebar-nav"') : html.index("</nav>")]
    for label in ("Today", "Course overview", "Chapters", "Sources", "Students"):
        assert label not in nav


def test_sidebar_footer(client, teacher):
    signed_in(client, teacher)
    html = page(client, "/subjects")
    assert 'href="/subjects"' in html
    assert 'href="/settings"' in html
    assert "Ollama server not checked" in html
    assert "Aamina" in html


def test_set_theme_persists_across_pages(client, teacher):
    signed_in(client, teacher)
    response = post(client, "/settings/theme", theme="dark", next="/subjects")
    assert response.status_code == 302
    assert response.headers["Location"] == "/subjects"
    assert teacher.theme == "dark"
    for path in ("/subjects", "/settings"):
        assert 'data-theme="dark"' in page(client, path)
    html = page(client, "/subjects")
    assert re.search(r'value="dark" aria-pressed="true"', html)


def test_match_system(client, teacher):
    signed_in(client, teacher)
    post(client, "/settings/theme", theme="dark")
    post(client, "/settings/theme", theme="system")
    html = page(client, "/settings")
    assert 'data-theme="system"' in html
    assert re.search(r'value="system" aria-pressed="true">Match system', html)


def test_invalid_theme_rejected(client, teacher):
    signed_in(client, teacher)
    assert post(client, "/settings/theme", theme="blue").status_code == 400
    assert teacher.theme == "system"


def test_theme_requires_csrf(client, teacher):
    signed_in(client, teacher)
    assert client.post("/settings/theme", data={"theme": "dark"}).status_code == 400
    assert teacher.theme == "system"


def test_unsafe_next_falls_back(client, teacher):
    signed_in(client, teacher)
    response = post(client, "/settings/theme", theme="dark", next="//evil.example")
    assert response.headers["Location"] == "/settings"


def test_next_keeps_query_string(client, teacher):
    signed_in(client, teacher)
    response = post(client, "/settings/theme", theme="dark", next="/subjects?x=1")
    assert response.headers["Location"] == "/subjects?x=1"


def test_theme_is_per_account(client, app, teacher, db_session):
    other = Teacher(
        email="bo@example.com", name="Bo", password_hash=hash_password("another pass")
    )
    db_session.add(other)
    db_session.commit()
    signed_in(client, teacher)
    post(client, "/settings/theme", theme="dark")
    second = app.test_client()
    sign_in(second, email="bo@example.com", password="another pass")
    assert 'data-theme="system"' in page(second, "/subjects")


def test_collapse_persists(client, teacher):
    signed_in(client, teacher)
    response = post(client, "/settings/sidebar", collapsed="1", next="/settings")
    assert response.status_code == 302
    assert response.headers["Location"] == "/settings"
    html = page(client, "/settings")
    assert "is-collapsed" in html
    assert 'aria-expanded="false"' in html
    assert "Expand sidebar" in html
    post(client, "/settings/sidebar", collapsed="0")
    html = page(client, "/settings")
    assert "is-collapsed" not in html
    assert "Collapse sidebar" in html


def test_invalid_collapse_rejected(client, teacher):
    signed_in(client, teacher)
    assert post(client, "/settings/sidebar", collapsed="2").status_code == 400
    assert teacher.sidebar_collapsed is False


def test_rail_items_have_label_and_tooltip(client, teacher):
    signed_in(client, teacher)
    post(client, "/settings/sidebar", collapsed="1")
    html = page(client, "/subjects")
    aside = html[html.index("<aside") : html.index("</aside>")]
    tags = re.findall(r"<(?:a|button)\b[^>]*>", aside)
    assert len(tags) >= 6
    for tag in tags:
        assert 'aria-label="' in tag, tag
        assert 'data-tip="' in tag, tag


def test_signed_out_post_redirects(client):
    token = csrf_token(client)
    response = client.post(
        "/settings/theme", data={"theme": "dark", "csrf_token": token}
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_uses_system_theme(client):
    assert 'data-theme="system"' in page(client, "/login")


def test_subject_pages_render_with_subject(app, teacher):
    with app.test_request_context("/subjects"):
        g.teacher = teacher
        html = render_template(
            "_sidebar.html",
            subject=SimpleNamespace(
                id=1,
                name="Physics",
                colour="#0E7A64",
                grade="Grade 11",
                class_minutes=60,
            ),
            active=None,
        )
    assert "Physics" in html
    for label in ("Today", "Course overview", "Chapters", "Sources", "Students"):
        assert label in html
    assert 'aria-disabled="true"' in html
    disabled = re.findall(r"<a [^>]*aria-disabled=\"true\"[^>]*>", html)
    assert len(disabled) == 5
    assert all('tabindex="0"' in tag and "href=" not in tag for tag in disabled)


def test_settings_page(client, teacher):
    signed_in(client, teacher)
    html = page(client, "/settings")
    for text in ("Appearance", "Light", "Dark", "Match system"):
        assert text in html
    assert '<p class="eyebrow">Chalkboard</p>' in html
    assert re.search(r'href="/settings"[^>]*aria-current="page"', html)
