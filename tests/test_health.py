from chalkboard import create_app
from chalkboard.config import Settings


def test_healthz_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_healthz_db_down():
    app = create_app(Settings(database_url="postgresql+psycopg://x:x@127.0.0.1:1/x"))
    response = app.test_client().get("/healthz")
    assert response.status_code == 503
    assert response.get_json()["status"] == "db unavailable"
