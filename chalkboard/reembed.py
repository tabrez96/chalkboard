"""Re-embed every chunk after the embedding model changes."""

import itertools

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from chalkboard import jobs, sources
from chalkboard.ai import model_client
from chalkboard.jobs import JobContext
from chalkboard.models import Chunk, Job, Source, Subject, Teacher

STEP = "Re-reading sources"


def has_chunks(session: Session, teacher: Teacher) -> bool:
    return bool(
        session.scalar(
            select(func.count())
            .select_from(Chunk)
            .join(Source, Chunk.source_id == Source.id)
            .join(Subject, Source.subject_id == Subject.id)
            .where(Subject.teacher_id == teacher.id)
            .limit(1)
        )
    )


def start_reembed(session: Session, teacher: Teacher) -> Job | None:
    """Queue the job and point ready sources at it; the caller commits."""
    if not has_chunks(session, teacher):
        return None
    job = jobs.enqueue(session, teacher.id, "reembed_all")
    subject_ids = select(Subject.id).where(Subject.teacher_id == teacher.id)
    session.execute(
        update(Source)
        .where(
            Source.subject_id.in_(subject_ids),
            Source.passages > 0,
            or_(
                Source.job_id.is_(None),
                Source.job_id.in_(
                    select(Job.id).where(
                        or_(
                            Job.state == "done",
                            (Job.kind == "reembed_all") & (Job.state == "failed"),
                        )
                    )
                ),
            ),
        )
        .values(job_id=job.id)
        .execution_options(synchronize_session=False)
    )
    return job


@jobs.register("reembed_all")
def reembed_all(ctx: JobContext) -> None:
    session = ctx.session
    rows = session.execute(
        select(Chunk.id, Chunk.text, Source.subject_id)
        .join(Source, Chunk.source_id == Source.id)
        .join(Subject, Source.subject_id == Subject.id)
        .where(Subject.teacher_id == ctx.teacher.id)
        .order_by(Source.subject_id, Chunk.id)
    ).all()
    if not rows:
        return
    total, done = len(rows), 0
    ctx.progress(STEP, 0)
    for subject_id, group in itertools.groupby(rows, key=lambda r: r.subject_id):
        client = model_client(ctx.teacher, session.get(Subject, subject_id))
        try:
            for batch in itertools.batched(list(group), sources.EMBED_BATCH):
                vectors = client.embed([r.text for r in batch])
                session.execute(
                    update(Chunk),
                    [
                        {"id": r.id, "embedding": v}
                        for r, v in zip(batch, vectors, strict=True)
                    ],
                )
                done += len(batch)
                ctx.progress(STEP, 100 * done // total)
        finally:
            client.close()
