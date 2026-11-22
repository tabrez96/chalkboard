from sqlalchemy import text


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
