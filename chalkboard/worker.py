import logging
import threading

from flask import Flask
from sqlalchemy.exc import SQLAlchemyError

from chalkboard.jobs import run_once

log = logging.getLogger(__name__)


def run_worker(
    app: Flask, poll_seconds: float = 2.0, stop: threading.Event | None = None
) -> None:
    """Run jobs until `stop` is set; a job in progress is always finished first."""
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            worked = run_once(app)
        except SQLAlchemyError:
            log.exception("Worker could not reach the database")
            worked = False
        if not worked:
            stop.wait(poll_seconds)
