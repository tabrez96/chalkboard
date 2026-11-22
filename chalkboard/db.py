from flask import Flask, current_app, g
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


SESSIONMAKER_KEY = "chalkboard.sessionmaker"


def init_db(app: Flask, database_url: str) -> None:
    engine = create_engine(database_url, pool_pre_ping=True)
    app.extensions[SESSIONMAKER_KEY] = sessionmaker(bind=engine, expire_on_commit=False)
    app.teardown_appcontext(close_session)


def get_session() -> Session:
    """The session for the current request/app context; created on first use."""
    if "db_session" not in g:
        g.db_session = current_app.extensions[SESSIONMAKER_KEY]()
    return g.db_session


def close_session(exc: BaseException | None = None) -> None:
    session = g.pop("db_session", None)
    if session is not None:
        session.close()
