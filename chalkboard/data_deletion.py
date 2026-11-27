"""Delete one subject, or all of a teacher's data, together with the stored files."""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from chalkboard.models import (
    AllowedSite,
    Job,
    PlanVersion,
    Source,
    Student,
    Subject,
    Teacher,
)
from chalkboard.sources import data_dir


def subject_counts(session: Session, subject: Subject) -> dict[str, int]:
    def count(model) -> int:
        return session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.subject_id == subject.id)
        )

    return {
        "sources": count(Source),
        "plans": count(PlanVersion),
        "students": count(Student),
    }


def _remove_files(paths: list[str]) -> None:
    root = data_dir()
    for path in paths:
        if path:
            (root / path).unlink(missing_ok=True)


def _run(session: Session, statement) -> None:
    session.execute(statement.execution_options(synchronize_session=False))


def delete_subject(session: Session, subject: Subject) -> None:
    # The lock an upload holds while it writes its file: no source can slip in after the paths are read.
    session.execute(
        select(Subject.id).where(Subject.id == subject.id).with_for_update()
    )
    paths = session.scalars(
        select(Source.file_path).where(Source.subject_id == subject.id)
    ).all()
    _run(session, delete(Subject).where(Subject.id == subject.id))
    session.commit()
    session.expire_all()
    _remove_files(list(paths))


def delete_everything(session: Session, teacher: Teacher) -> None:
    ids = select(Subject.id).where(Subject.teacher_id == teacher.id)
    session.execute(ids.order_by(Subject.id).with_for_update())
    paths = session.scalars(select(Source.file_path).where(Source.subject_id.in_(ids)))
    paths = list(paths.all())
    # Subjects first, so no plan version sees its status job disappear.
    _run(session, delete(Subject).where(Subject.teacher_id == teacher.id))
    _run(session, delete(AllowedSite).where(AllowedSite.teacher_id == teacher.id))
    _run(
        session,
        delete(Job).where(Job.teacher_id == teacher.id, Job.state != "running"),
    )
    session.commit()
    session.expire_all()
    _remove_files(paths)
