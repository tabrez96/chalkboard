import httpx
from flask import render_template_string

from chalkboard.ai import TRANSPORT_KEY
from tests.conftest import csrf_token, sign_in

BOTH = ["qwen3-embedding:0.6b", "qwen3.5:9b"]


def serve(app, models=BOTH, hosts=None):
    """Mock the Ollama server: /api/tags lists `models`; a list given as `hosts` records each request host."""

    def handler(request):
        if hosts is not None:
            hosts.append(request.url.host)
        if models is None:
            raise httpx.ConnectError("down")
        return httpx.Response(
            200, json={"models": [{"name": m, "model": m} for m in models]}
        )

    app.extensions[TRANSPORT_KEY] = httpx.MockTransport(handler)


def form(client, action, **values):
    data = {
        "ollama_url": "http://localhost:11434",
        "embed_model": "qwen3-embedding:0.6b",
        "write_model": "qwen3.5:9b",
        "action": action,
        "csrf_token": csrf_token(client, "/settings"),
    }
    return client.post("/settings/ollama", data=data | values)


def signed_in(client, teacher, app, **serve_args):
    serve(app, **serve_args)
    sign_in(client)
    return client


def text(response):
    return response.get_data(as_text=True)


def test_defaults_shown(client, teacher, app):
    html = text(signed_in(client, teacher, app).get("/settings"))
    for value in ("qwen3-embedding:0.6b", "qwen3.5:9b", "http://localhost:11434"):
        assert f'value="{value}"' in html
    assert "Not checked" in html


def test_test_connection_ok(client, teacher, app):
    html = text(form(signed_in(client, teacher, app), "test"))
    assert "Connected." in html and "Both models are installed" in html
    assert 'role="status"' in html


def test_test_connection_unreachable(client, teacher, app):
    html = text(form(signed_in(client, teacher, app, models=None), "test"))
    assert "No answer from localhost:11434." in html
    assert "Nothing was changed." in html
    assert 'role="alert"' in html and "Not connected" in html


def test_test_connection_model_missing(client, teacher, app):
    html = text(
        form(signed_in(client, teacher, app, models=["qwen3-embedding:0.6b"]), "test")
    )
    assert "qwen3.5:9b isn&#39;t installed" in html
    assert "ollama pull qwen3.5:9b" in html
    both = text(form(client, "test"))
    assert "ollama pull qwen3.5:9b" in both


def test_test_records_status_for_saved_settings_only(client, teacher, app, db_session):
    signed_in(client, teacher, app)
    form(client, "test", write_model="other:1b")
    db_session.refresh(teacher)
    assert teacher.ollama_status == "unknown"
    form(client, "test")
    db_session.refresh(teacher)
    assert teacher.ollama_status == "ok"


def test_test_does_not_save(client, teacher, app, db_session):
    signed_in(client, teacher, app)
    form(client, "test", ollama_url="http://other.test:1", write_model="other:1b")
    db_session.refresh(teacher)
    assert (teacher.ollama_url, teacher.embed_model, teacher.write_model) == (None,) * 3


def test_save_changes_settings_without_code(client, teacher, app, db_session):
    hosts = []
    signed_in(client, teacher, app, hosts=hosts)
    teacher.ollama_status = "ok"
    db_session.commit()
    values = {
        "ollama_url": "http://gpu.test:11434/",
        "embed_model": "nomic-embed",
        "write_model": "llama3:8b",
    }
    response = form(client, "save", **values)
    assert response.status_code == 302
    db_session.refresh(teacher)
    assert teacher.ollama_url == "http://gpu.test:11434"
    assert (teacher.embed_model, teacher.write_model) == ("nomic-embed", "llama3:8b")
    assert teacher.ollama_status == "unknown"
    assert "Saved." in text(client.get("/settings"))
    serve(app, models=["nomic-embed:latest", "llama3:8b"], hosts=hosts)
    html = text(
        client.post(
            "/settings/ollama",
            data={
                **values,
                "ollama_url": "http://gpu.test:11434",
                "action": "test",
                "csrf_token": csrf_token(client, "/settings"),
            },
        )
    )
    assert hosts == ["gpu.test"] and "Connected." in html


def test_save_unchanged_keeps_status(client, teacher, app, db_session):
    signed_in(client, teacher, app)
    teacher.ollama_status = "ok"
    db_session.commit()
    form(client, "save")
    db_session.refresh(teacher)
    assert teacher.ollama_status == "ok" and teacher.ollama_url is None


def test_invalid_input_rejected(client, teacher, app):
    hosts = []
    signed_in(client, teacher, app, hosts=hosts)
    for values in (
        {"ollama_url": "ftp://x"},
        {"ollama_url": "not a url"},
        {"ollama_url": "http://["},
        {"embed_model": "bad name!"},
        {"write_model": ""},
    ):
        response = form(client, "test", **values)
        assert response.status_code == 400
        assert 'class="field-error"' in text(response)
    assert hosts == []


def test_unknown_action_rejected(client, teacher, app):
    assert form(signed_in(client, teacher, app), "nope").status_code == 400


def test_sidebar_dot_follows_status(client, teacher, app, db_session):
    signed_in(client, teacher, app)
    expected = {
        "unknown": ("Ollama server not checked", "not checked yet"),
        "ok": ("Ollama server connected", "connected"),
        "unreachable": ("Ollama server unreachable", "unreachable"),
        "model_missing": ("Ollama model missing", "a model is missing"),
    }
    for status, (label, words) in expected.items():
        teacher.ollama_status = status
        db_session.commit()
        html = text(client.get("/settings"))
        assert label in html
        assert f'aria-label="Ollama server: {words}"' in html


def test_banner_only_when_unreachable(app, teacher):
    template = '{% from "_ollama.html" import ollama_banner %}{{ ollama_banner(t) }}'
    with app.test_request_context("/"):
        teacher.ollama_status = "ok"
        assert render_template_string(template, t=teacher).strip() == ""
        teacher.ollama_status = "unreachable"
        html = render_template_string(template, t=teacher)
    assert "Can't reach the Ollama server at localhost:11434" in html
    assert "Check connection" in html


def test_check_connection_records_status(client, teacher, app, db_session):
    signed_in(client, teacher, app)
    teacher.ollama_status = "unreachable"
    db_session.commit()
    token = csrf_token(client, "/settings")
    response = client.post("/settings/ollama/check", data={"csrf_token": token})
    assert response.status_code == 200 and "Connected." in text(response)
    db_session.refresh(teacher)
    assert teacher.ollama_status == "ok"
    assert client.post("/settings/ollama/check").status_code == 400
