from flask import Blueprint
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from chalkboard.auth import public
from chalkboard.db import get_session

bp = Blueprint("health", __name__)


@bp.get("/healthz")
@public
def healthz():
    try:
        get_session().execute(text("SELECT 1"))
    except OperationalError:
        return {"status": "db unavailable"}, 503
    return {"status": "ok"}
