import logging
import threading
import time

from flask import Flask
from sqlalchemy.exc import SQLAlchemyError

from chalkboard.backup import daily_backup
from chalkboard.jobs import run_once

log = logging.getLogger(__name__)

BACKUP_CHECK_SECONDS = 300


def run_worker(
    app: Flask, poll_seconds: float = 2.0, stop: threading.Event | None = None
) -> None:
    """Run jobs until `stop` is set; a job in progress is always finished first."""
    stop = stop or threading.Event()
    next_check = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_check:
            next_check = time.monotonic() + BACKUP_CHECK_SECONDS
            try:
                daily_backup(app)
            except Exception:
                log.exception("Daily backup failed")
        try:
            worked = run_once(app)
        except SQLAlchemyError:
            log.exception("Worker could not reach the database")
            worked = False
        if not worked:
            stop.wait(poll_seconds)
