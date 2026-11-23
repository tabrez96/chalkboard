import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from chalkboard import jobs
from chalkboard.ai.base import ModelMissing, OllamaUnreachable
from chalkboard.db import get_session
from chalkboard.models import Job, Teacher
from chalkboard.worker import run_worker
from tests.conftest import sign_in
from tests.test_settings_ollama import serve

LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(jobs, "REGISTRY", {})


@pytest.fixture
def committed_teacher(engine):
    """A teacher committed for real (separate connections need to see it); removed with its jobs afterwards."""
    with Session(engine) as session:
        teacher = Teacher(email="conc@example.com", name="Conc", password_hash="x")
        session.add(teacher)
        session.commit()
        teacher_id = teacher.id
    yield teacher_id
    with Session(engine) as session:
        session.delete(session.get(Teacher, teacher_id))
        session.commit()


def make_job(db_session, teacher, kind="demo", **fields):
    job = Job(teacher_id=teacher.id, kind=kind, **fields)
    db_session.add(job)
    db_session.commit()
    return job


def run(app, db_session, job):
    result = jobs.run_once(app)
    db_session.refresh(job)
    return result


def test_backoff_seconds():
    assert [jobs.backoff_seconds(n) for n in (1, 2, 3, 10)] == [10, 40, 160, 600]


def test_enqueue_unknown_kind_raises(db_session, teacher):
    with pytest.raises(ValueError):
        jobs.enqueue(db_session, teacher.id, "nope")


def test_enqueue_adds_queued_job(db_session, teacher):
    jobs.register("demo")(lambda ctx: None)
    job = jobs.enqueue(db_session, teacher.id, "demo", {"a": 1})
    db_session.commit()
    db_session.refresh(job)
    assert (job.state, job.payload, job.progress) == ("queued", {"a": 1}, 0)


def test_claim_skips_row_locked_by_another_worker(engine, committed_teacher):
    with Session(engine) as setup:
        setup.add_all(Job(teacher_id=committed_teacher, kind="demo") for _ in range(2))
        setup.commit()
        first, second = setup.scalars(
            select(Job.id).where(Job.teacher_id == committed_teacher).order_by(Job.id)
        )
    with engine.connect() as holder:
        holder.begin()
        holder.execute(select(Job).where(Job.id == first).with_for_update())
        with Session(engine) as worker:
            job, _ = jobs.claim(worker)
            assert (job.id, job.state) == (second, "running")
        holder.rollback()


def test_two_workers_never_run_the_same_job(engine, committed_teacher):
    with Session(engine) as setup:
        setup.add_all(Job(teacher_id=committed_teacher, kind="demo") for _ in range(20))
        setup.commit()
    barrier = threading.Barrier(2)
    claimed: list[list[int]] = [[], []]

    def work(index):
        with Session(engine) as session:
            barrier.wait()
            while (got := jobs.claim(session)) is not None:
                claimed[index].append(got[0].id)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not set(claimed[0]) & set(claimed[1])
    assert len(claimed[0]) + len(claimed[1]) == 20


def test_claim_ignores_future_and_finished_jobs(db_session, teacher):
    from sqlalchemy import func

    make_job(db_session, teacher, run_after=func.now() + func.make_interval(0, 0, 0, 1))
    make_job(db_session, teacher, state="done")
    make_job(db_session, teacher, state="failed")
    make_job(db_session, teacher, state="running", heartbeat_at=func.now())
    assert jobs.claim(db_session) is None


def test_claim_reclaims_stale_running_job(db_session, teacher):
    job = make_job(db_session, teacher, state="running", heartbeat_at=LONG_AGO)
    claimed, _ = jobs.claim(db_session)
    assert claimed.id == job.id


def test_run_once_runs_handler_and_marks_done(app, db_session, teacher):
    serve(app)
    seen = []
    jobs.register("demo")(lambda ctx: seen.append(ctx.payload))
    job = make_job(db_session, teacher, payload={"x": 1})
    assert run(app, db_session, job) is True
    db_session.refresh(teacher)
    assert seen == [{"x": 1}]
    assert (job.state, job.progress, job.error) == ("done", 100, None)
    assert teacher.ollama_status == "ok"
    assert jobs.run_once(app) is False


def test_progress_reports_step_and_percent(app, db_session, teacher):
    serve(app)
    stored = []

    @jobs.register("demo")
    def handler(ctx):
        ctx.progress("Reading pages", 40)
        stored.append(
            db_session.execute(
                select(Job.step, Job.progress).where(Job.id == ctx.job.id)
            ).one()
        )
        ctx.progress("x", 150)
        stored.append(
            db_session.execute(select(Job.progress).where(Job.id == ctx.job.id)).one()
        )

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert tuple(stored[0]) == ("Reading pages", 40)
    assert tuple(stored[1]) == (100,)


def test_progress_heartbeat_uses_wall_clock_not_transaction_start(
    app, db_session, teacher
):
    serve(app)
    later = []

    @jobs.register("demo")
    def handler(ctx):
        ctx.progress("Step", 10)
        later.append(
            db_session.scalar(
                select(Job.heartbeat_at > func.now()).where(Job.id == ctx.job.id)
            )
        )

    run(app, db_session, make_job(db_session, teacher))
    assert later == [True]


def reclaim(session, job_id):
    """Simulate another worker claiming the job while this one still runs it."""
    session.execute(update(Job).where(Job.id == job_id).values(claims=Job.claims + 1))
    session.commit()


def test_claim_increments_claims(db_session, teacher):
    make_job(db_session, teacher)
    assert jobs.claim(db_session)[1] == 1
    db_session.execute(update(Job).values(heartbeat_at=LONG_AGO))
    db_session.commit()
    assert jobs.claim(db_session)[1] == 2


def test_reclaimed_job_ignores_old_executors_progress(app, db_session, teacher):
    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        reclaim(ctx.session, ctx.job.id)
        ctx.progress("late", 90)

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert (job.state, job.step, job.progress, job.claims) == ("running", "", 0, 2)


def test_reclaimed_job_ignores_old_executors_final_state(app, db_session, teacher):
    serve(app)
    jobs.register("demo")(lambda ctx: reclaim(ctx.session, ctx.job.id))
    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert (job.state, job.progress, job.claims) == ("running", 0, 2)


def test_reclaimed_job_ignores_old_executors_failure(app, db_session, teacher):
    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        reclaim(ctx.session, ctx.job.id)
        raise RuntimeError("boom")

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert (job.state, job.attempts, job.error) == ("running", 0, None)


def test_unreachable_ollama_parks_job_and_resumes(app, db_session, teacher):
    serve(app, models=None)
    calls = []
    jobs.register("demo")(lambda ctx: calls.append(1))
    job = make_job(db_session, teacher)
    run(app, db_session, job)
    db_session.refresh(teacher)
    assert job.state == "waiting_ollama"
    assert job.error == jobs.WAITING_UNREACHABLE
    assert calls == [] and job.attempts == 0
    assert teacher.ollama_status == "unreachable"
    assert job.run_after > job.created_at

    serve(app)
    job.run_after = LONG_AGO
    db_session.commit()
    run(app, db_session, job)
    db_session.refresh(teacher)
    assert job.state == "done"
    assert calls == [1]
    assert teacher.ollama_status == "ok"


def test_model_missing_parks_job(app, db_session, teacher):
    serve(app, models=["qwen3.5:9b"])
    jobs.register("demo")(lambda ctx: None)
    job = make_job(db_session, teacher)
    run(app, db_session, job)
    db_session.refresh(teacher)
    assert job.state == "waiting_ollama"
    assert "qwen3-embedding:0.6b" in job.error
    assert teacher.ollama_status == "model_missing"


def test_handler_losing_ollama_parks_job(app, db_session, teacher):
    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        raise OllamaUnreachable

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    db_session.refresh(teacher)
    assert (job.state, job.attempts) == ("waiting_ollama", 0)
    assert teacher.ollama_status == "unreachable"


def test_handler_missing_model_parks_job(app, db_session, teacher):
    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        raise ModelMissing("qwen3.5:9b")

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    db_session.refresh(teacher)
    assert job.state == "waiting_ollama"
    assert job.error == "Waiting for qwen3.5:9b on the Ollama server"
    assert teacher.ollama_status == "model_missing"


def test_failing_job_retries_with_backoff_then_fails(app, db_session, teacher):
    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        raise RuntimeError("boom")

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert (job.state, job.attempts, job.error) == ("queued", 1, jobs.GENERIC_FAILURE)
    assert jobs.run_once(app) is False  # backing off
    job.run_after = LONG_AGO
    db_session.commit()
    run(app, db_session, job)
    assert (job.state, job.attempts) == ("queued", 2)
    job.run_after = LONG_AGO
    db_session.commit()
    run(app, db_session, job)
    assert (job.state, job.attempts) == ("failed", 3)
    assert job.error == jobs.GENERIC_FAILURE


def test_job_failed_is_not_retried(app, db_session, teacher):
    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        raise jobs.JobFailed("Couldn't read the file")

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert (job.state, job.error) == ("failed", "Couldn't read the file")


def test_ollama_error_retries_with_its_message(app, db_session, teacher):
    from chalkboard.ai.base import OllamaError

    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        raise OllamaError("Ollama sent an unreadable answer")

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert (job.state, job.error) == ("queued", "Ollama sent an unreadable answer")


def test_handler_work_after_last_progress_is_rolled_back(app, db_session, teacher):
    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        ctx.progress("Step", 30)
        ctx.job.step = "unsaved"
        raise RuntimeError("boom")

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert (job.step, job.progress) == ("Step", 30)


def test_unknown_kind_fails(app, db_session, teacher):
    job = make_job(db_session, teacher, kind="gone")
    run(app, db_session, job)
    assert (job.state, job.error) == ("failed", jobs.UNKNOWN_KIND)


def test_job_without_ollama_skips_health_check(app, db_session, teacher):
    serve(app, models=None)
    jobs.register("demo", needs_ollama=False)(lambda ctx: None)
    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert job.state == "done"


def signed_in(client, teacher):
    sign_in(client)
    return client


def test_status_partial_for_running_job(client, db_session, teacher):
    job = make_job(
        db_session, teacher, state="running", step="Reading pages", progress=40
    )
    signed_in(client, teacher)
    html = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert 'role="status"' in html
    assert "Reading pages · 40%" in html
    assert 'hx-trigger="every 2s"' in html
    assert f'hx-get="/jobs/{job.id}"' in html


def test_status_partial_for_finished_job_stops_polling(client, db_session, teacher):
    done = make_job(db_session, teacher, state="done")
    failed = make_job(
        db_session, teacher, state="failed", error="Couldn't read the file"
    )
    signed_in(client, teacher)
    html = client.get(f"/jobs/{done.id}").get_data(as_text=True)
    assert "Done" in html and "hx-trigger" not in html
    html = client.get(f"/jobs/{failed.id}").get_data(as_text=True)
    assert 'role="alert"' in html and "Couldn&#39;t read the file" in html
    assert "hx-trigger" not in html


def test_status_partial_waiting_ollama(client, db_session, teacher):
    job = make_job(db_session, teacher, state="waiting_ollama")
    signed_in(client, teacher)
    html = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "Saved · waiting for Ollama" in html
    assert "hx-trigger" in html


def test_status_partial_queued(client, db_session, teacher):
    job = make_job(db_session, teacher)
    signed_in(client, teacher)
    assert "Queued" in client.get(f"/jobs/{job.id}").get_data(as_text=True)


def test_status_of_other_teachers_job_is_404(client, db_session, teacher):
    other = Teacher(email="b@example.com", name="B", password_hash="x")
    db_session.add(other)
    db_session.commit()
    job = make_job(db_session, other)
    response = client.get(f"/jobs/{job.id}")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    signed_in(client, teacher)
    assert client.get(f"/jobs/{job.id}").status_code == 404
    assert client.get("/jobs/999999").status_code == 404


def test_worker_loop_stops(app):
    stop = threading.Event()
    thread = threading.Thread(target=run_worker, args=(app, 0.01, stop))
    thread.start()
    stop.set()
    thread.join(2)
    assert not thread.is_alive()


def test_worker_command_registered(app):
    assert app.cli.get_command(None, "worker") is not None


def test_stale_claim_number_does_not_park_job(app, db_session, teacher):
    # The claim number comes from claim(), not from the job row at execute time:
    # an executor holding an old number writes nothing, even when it parks before the handler.
    serve(app, models=None)
    jobs.register("demo")(lambda ctx: pytest.fail("handler must not run"))
    job = make_job(db_session, teacher)
    _, claimed = jobs.claim(db_session)
    reclaim(db_session, job.id)
    with app.app_context():
        session = get_session()
        jobs.execute(session, session.get(Job, job.id), claimed)
    db_session.refresh(job)
    assert (job.state, job.error, job.claims) == ("running", None, claimed + 1)


def test_unknown_kind_with_stale_claim_writes_nothing(db_session, teacher):
    job = make_job(db_session, teacher, kind="gone")
    _, claimed = jobs.claim(db_session)
    reclaim(db_session, job.id)
    jobs.execute(db_session, job, claimed)
    db_session.refresh(job)
    assert (job.state, job.error) == ("running", None)


def test_unflushed_bad_row_is_retried(app, db_session, teacher):
    serve(app)

    @jobs.register("demo")
    def handler(ctx):
        ctx.session.add(Job(teacher_id=teacher.id, kind="demo", state="bogus"))

    job = make_job(db_session, teacher)
    run(app, db_session, job)
    assert (job.state, job.attempts, job.error) == (
        "queued",
        1,
        jobs.GENERIC_FAILURE,
    )
