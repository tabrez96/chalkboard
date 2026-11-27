import os
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from chalkboard import create_app
from chalkboard.config import Settings
from chalkboard.db import SESSIONMAKER_KEY
from chalkboard.models import Teacher
from chalkboard.passwords import hash_password

TEST_DATABASE_URL = os.environ.get(
    "CHALKBOARD_TEST_DATABASE_URL",
    "postgresql+psycopg://chalkboard:chalkboard@localhost:5432/chalkboard_test",
)


def recreate_database(url: str) -> None:
    target = make_url(url)
    if not (target.database or "").endswith("_test"):
        raise RuntimeError(
            f"Refusing to drop {target.database!r}: the test database name must end with '_test'"
        )
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


def run_migrations(url: str) -> None:
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def engine():
    recreate_database(TEST_DATABASE_URL)  # every run starts from an empty database
    run_migrations(TEST_DATABASE_URL)
    eng = create_engine(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def db_connection(engine):
    connection = engine.connect()
    transaction = connection.begin()
    yield connection
    transaction.rollback()
    connection.close()


@pytest.fixture
def db_session(db_connection):
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()


def make_app(db_connection):
    app = create_app(
        Settings(database_url=TEST_DATABASE_URL, secret_key="test", daily_backup=False)
    )
    app.config["TESTING"] = True
    # Route every request's session into the per-test transaction; commits become savepoints.
    app.extensions[SESSIONMAKER_KEY] = sessionmaker(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    return app


@pytest.fixture
def app(db_connection):
    return make_app(db_connection)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def teacher(db_session):
    teacher = Teacher(
        email="aamina@example.com",
        name="Aamina",
        password_hash=hash_password("correct horse"),
    )
    db_session.add(teacher)
    db_session.commit()
    return teacher


def csrf_token(client, path="/login") -> str:
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def sign_in(client, email="aamina@example.com", password="correct horse", next=None):
    token = csrf_token(client)
    return client.post(
        "/login",
        query_string={"next": next} if next else None,
        data={"email": email, "password": password, "csrf_token": token},
    )
