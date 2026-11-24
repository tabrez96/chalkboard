from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import TEST_DATABASE_URL, recreate_database, run_migrations


def test_vector_extension_installed(db_session):
    rows = db_session.execute(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    ).all()
    assert len(rows) == 1


def test_vector_type_usable(db_session):
    assert db_session.execute(text("SELECT '[1,2,3]'::vector")).scalar() is not None


def test_rollback_probe_a_creates(db_session):
    db_session.execute(text("CREATE TABLE rollback_probe (id int)"))
    db_session.commit()


def test_rollback_probe_b_is_gone(db_session):
    assert (
        db_session.execute(text("SELECT to_regclass('rollback_probe')")).scalar()
        is None
    )


def test_teachers_table_exists(db_session):
    assert (
        db_session.execute(text("SELECT to_regclass('teachers')")).scalar() is not None
    )


def test_chunks_embedding_is_vector_1024_with_hnsw(db_session):
    column = db_session.execute(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        )
    ).scalar()
    index = db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_chunks_embedding'")
    ).scalar()
    assert column == "vector(1024)"
    assert "hnsw" in index
    assert "vector_cosine_ops" in index


def test_0008_downgrades_and_upgrades():
    base = make_url(TEST_DATABASE_URL)
    url = base.set(database=base.database.removesuffix("_test") + "_migrations_test")
    url_text = url.render_as_string(hide_password=False)
    recreate_database(url_text)
    run_migrations(url_text)
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.attributes["database_url"] = url_text
    engine = create_engine(url)
    try:
        command.downgrade(cfg, "0007")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('sources')")).scalar() is None
            assert conn.execute(text("SELECT to_regclass('chunks')")).scalar() is None
            assert not conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'subjects' AND column_name = 'source_counter'"
                )
            ).all()
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('sources')")).scalar()
            assert conn.execute(text("SELECT to_regclass('chunks')")).scalar()
    finally:
        engine.dispose()
        admin = create_engine(
            url.set(database="postgres"), isolation_level="AUTOCOMMIT"
        )
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        admin.dispose()


def test_0009_downgrades_and_upgrades():
    base = make_url(TEST_DATABASE_URL)
    url = base.set(database=base.database.removesuffix("_test") + "_migrations_test")
    url_text = url.render_as_string(hide_password=False)
    recreate_database(url_text)
    run_migrations(url_text)
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.attributes["database_url"] = url_text
    engine = create_engine(url)
    column = text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'sources' AND column_name = 'is_syllabus'"
    )
    try:
        command.downgrade(cfg, "0008")
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT to_regclass('syllabus_units')")).scalar()
                is None
            )
            assert not conn.execute(column).all()
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('syllabus_units')")).scalar()
            assert conn.execute(column).all()
    finally:
        engine.dispose()
        admin = create_engine(
            url.set(database="postgres"), isolation_level="AUTOCOMMIT"
        )
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        admin.dispose()


def test_0010_downgrades_and_upgrades():
    base = make_url(TEST_DATABASE_URL)
    url = base.set(database=base.database.removesuffix("_test") + "_migrations_test")
    url_text = url.render_as_string(hide_password=False)
    recreate_database(url_text)
    run_migrations(url_text)
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.attributes["database_url"] = url_text
    engine = create_engine(url)
    try:
        command.downgrade(cfg, "0009")
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT to_regclass('plan_versions')")).scalar()
                is None
            )
            assert (
                conn.execute(
                    text("SELECT to_regproc('reject_content_update')")
                ).scalar()
                is None
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('plan_versions')")).scalar()
            assert conn.execute(
                text("SELECT to_regproc('reject_content_update')")
            ).scalar()
    finally:
        engine.dispose()
        admin = create_engine(
            url.set(database="postgres"), isolation_level="AUTOCOMMIT"
        )
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        admin.dispose()


def test_0011_downgrades_and_upgrades():
    base = make_url(TEST_DATABASE_URL)
    url = base.set(database=base.database.removesuffix("_test") + "_migrations_test")
    url_text = url.render_as_string(hide_password=False)
    recreate_database(url_text)
    run_migrations(url_text)
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.attributes["database_url"] = url_text
    engine = create_engine(url)
    query = text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'syllabus_units' AND column_name = 'other_term'"
    )
    try:
        command.downgrade(cfg, "0010")
        with engine.connect() as conn:
            assert not conn.execute(query).all()
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(query).all()
    finally:
        engine.dispose()
        admin = create_engine(
            url.set(database="postgres"), isolation_level="AUTOCOMMIT"
        )
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        admin.dispose()


def test_0012_downgrades_and_upgrades():
    base = make_url(TEST_DATABASE_URL)
    url = base.set(database=base.database.removesuffix("_test") + "_migrations_test")
    url_text = url.render_as_string(hide_password=False)
    recreate_database(url_text)
    run_migrations(url_text)
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.attributes["database_url"] = url_text
    engine = create_engine(url)
    query = text("SELECT to_regclass('added_chapters')")
    try:
        command.downgrade(cfg, "0011")
        with engine.connect() as conn:
            assert conn.execute(query).scalar() is None
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert conn.execute(query).scalar() is not None
    finally:
        engine.dispose()
        admin = create_engine(
            url.set(database="postgres"), isolation_level="AUTOCOMMIT"
        )
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        admin.dispose()
