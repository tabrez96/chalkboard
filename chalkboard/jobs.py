"""Job queue: the registry, claiming, running, and the status partial.

Later tickets register their job kinds with `@register(...)`; the worker
(`chalkboard.worker`) claims and runs them.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from flask import Blueprint, Flask, abort, render_template
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from chalkboard.ai import Health, HealthState, model_client, record_status
from chalkboard.ai.base import ModelMissing, OllamaError, OllamaUnreachable
from chalkboard.auth import teacher_select
from chalkboard.db import get_session
from chalkboard.models import Job, Teacher

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
OLLAMA_RECHECK_SECONDS = 30
# Longer than ai.ollama.TIMEOUT (600 s), so a slow model call is not mistaken for a dead worker.
STALE_AFTER_SECONDS = 900
ACTIVE_STATES = ("queued", "running", "waiting_ollama")
WAITING_UNREACHABLE = "Waiting for the Ollama server"
GENERIC_FAILURE = "Something went wrong. Try again."
UNKNOWN_KIND = "This kind of job is not supported"

bp = Blueprint("jobs", __name__)


class JobFailed(Exception):
    """A permanent failure: the message is shown to the teacher and the job is not retried."""


class JobLost(Exception):
    """Another worker reclaimed the job; this executor must stop and write nothing more."""


@dataclass(frozen=True)
class JobKind:
    name: str
    handler: Callable[[JobContext], None]
    needs_ollama: bool = True


REGISTRY: dict[str, JobKind] = {}


def register(name: str, *, needs_ollama: bool = True) -> Callable:
    def decorator(fn: Callable) -> Callable:
        REGISTRY[name] = JobKind(name, fn, needs_ollama)
        return fn

    return decorator


@dataclass
class JobContext:
    """What a handler gets. Handlers may be re-run after a crash or retry, so they must be idempotent."""

    session: Session
    job: Job
    teacher: Teacher
    # The claim number this executor holds; see `Job.claims`.
    claim: int

    @property
    def payload(self) -> dict:
        return self.job.payload

    def progress(self, step: str, percent: int) -> None:
        """Save step and percent; raises `JobLost` if another worker reclaimed the job."""
        result = self.session.execute(
            update(Job)
            .where(Job.id == self.job.id, Job.claims == self.claim)
            .values(
                step=step[:80],
                progress=max(0, min(100, percent)),
                # clock_timestamp(), not now(): now() is the start of the transaction, which may be long ago.
                heartbeat_at=func.clock_timestamp(),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            self.session.rollback()
            raise JobLost
        self.session.commit()


def backoff_seconds(attempts: int) -> int:
    return min(10 * 4 ** (attempts - 1), 600)


def enqueue(
    session: Session, teacher_id: int, kind: str, payload: dict | None = None
) -> Job:
    """Add a job; the caller commits."""
    if kind not in REGISTRY:
        raise ValueError(f"Unknown job kind: {kind}")
    job = Job(teacher_id=teacher_id, kind=kind, payload=payload or {})
    session.add(job)
    session.flush()
    return job


def claim(session: Session) -> tuple[Job, int] | None:
    """Mark the next due job running; two workers never get the same one.

    Returns the job and its claim number, read while the row lock is held."""
    stale = func.now() - timedelta(seconds=STALE_AFTER_SECONDS)
    job = session.scalar(
        select(Job)
        .where(
            or_(
                and_(
                    Job.state.in_(("queued", "waiting_ollama")),
                    Job.run_after <= func.now(),
                ),
                and_(Job.state == "running", Job.heartbeat_at < stale),
            )
        )
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        session.rollback()
        return None
    job.state = "running"
    job.claims = Job.claims + 1
    job.heartbeat_at = func.clock_timestamp()
    session.flush()
    claimed = job.claims
    session.commit()
    return job, claimed


def _park(job: Job, message: str) -> None:
    job.state = "waiting_ollama"
    job.error = message[:500]
    job.step = ""
    job.run_after = func.clock_timestamp() + timedelta(seconds=OLLAMA_RECHECK_SECONDS)


def _fail(job: Job, message: str) -> None:
    job.state = "failed"
    job.error = message[:500]


def _retry_or_fail(job: Job, message: str) -> None:
    job.attempts += 1
    if job.attempts >= MAX_ATTEMPTS:
        _fail(job, message)
        return
    job.state = "queued"
    job.error = message[:500]
    job.run_after = func.clock_timestamp() + timedelta(
        seconds=backoff_seconds(job.attempts)
    )


def _waiting_message(health: Health) -> str:
    if health.state == HealthState.UNREACHABLE:
        return WAITING_UNREACHABLE
    return f"Waiting for {', '.join(health.missing)} on the Ollama server"


def execute(session: Session, job: Job, claimed: int) -> None:
    """Run one claimed job inside an app context and record how it ended."""
    job_id, job_kind, teacher_id = job.id, job.kind, job.teacher_id

    def lock() -> Job | None:
        # Re-read with a row lock; None (and nothing written) if another worker reclaimed the job meanwhile.
        fresh = session.scalar(
            select(Job)
            .where(Job.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if fresh.claims != claimed:
            session.rollback()
            return None
        return fresh

    kind = REGISTRY.get(job_kind)
    if kind is None:
        if (job := lock()) is not None:
            _fail(job, UNKNOWN_KIND)
            session.commit()
        return
    teacher = session.get(Teacher, teacher_id)
    if kind.needs_ollama:
        client = model_client(teacher)
        try:
            health = client.health()
        finally:
            client.close()
        record_status(teacher, health)
        if health.state != HealthState.OK:
            if (job := lock()) is not None:
                _park(job, _waiting_message(health))
                session.commit()
            return
        session.commit()

    def park_unreachable(job: Job, teacher: Teacher) -> None:
        record_status(teacher, Health(HealthState.UNREACHABLE))
        _park(job, WAITING_UNREACHABLE)

    def park_missing(model: str) -> Callable[[Job, Teacher], None]:
        def finish(job: Job, teacher: Teacher) -> None:
            record_status(teacher, Health(HealthState.MODEL_MISSING, (model,)))
            _park(job, f"Waiting for {model} on the Ollama server")

        return finish

    def mark_done(job: Job, teacher: Teacher) -> None:
        job.state, job.progress, job.error = "done", 100, None

    # Each outcome is applied the same way: drop unsaved handler work (except on success), lock the row,
    # check the claim, then write the final state.
    rollback = True
    try:
        kind.handler(JobContext(session, job, teacher, claimed))
        # Surface bad handler writes here, where the retry path catches them, not in lock().
        session.flush()
        finish, rollback = mark_done, False
    except JobLost:
        session.rollback()
        log.warning("Job %s (%s) was reclaimed by another worker", job_id, kind.name)
        return
    except OllamaUnreachable:
        finish = park_unreachable
    except ModelMissing as exc:
        finish = park_missing(exc.model)
    except JobFailed as exc:
        message = str(exc)
        finish = lambda job, teacher: _fail(job, message)
    except OllamaError as exc:
        message = str(exc)
        log.warning("Job %s (%s) hit an Ollama error: %s", job_id, kind.name, exc)
        finish = lambda job, teacher: _retry_or_fail(job, message)
    except Exception:
        log.exception("Job %s (%s) failed", job_id, kind.name)
        finish = lambda job, teacher: _retry_or_fail(job, GENERIC_FAILURE)
    if rollback:
        session.rollback()
    job = lock()
    if job is None:
        return
    finish(job, session.get(Teacher, job.teacher_id))
    session.commit()


def run_once(app: Flask) -> bool:
    """Claim and run one job; False when none is due. One app context per job, so each gets a fresh session."""
    with app.app_context():
        session = get_session()
        claimed = claim(session)
        if claimed is None:
            return False
        execute(session, *claimed)
        return True


@bp.get("/jobs/<int:job_id>")
def status(job_id):
    job = get_session().scalar(teacher_select(Job).where(Job.id == job_id))
    if job is None:
        abort(404)
    return render_template(
        "jobs/_status.html", job=job, active=job.state in ACTIVE_STATES
    )
