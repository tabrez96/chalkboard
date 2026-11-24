"""The syllabus: one source per subject, its units, and the job that reads them."""

import re
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from chalkboard import jobs
from chalkboard.ai import model_client
from chalkboard.ai.base import FormatMismatch, LLMClient
from chalkboard.extract import ExtractError, extract, kind_for
from chalkboard.jobs import JobContext
from chalkboard.models import Job, Source, Subject, SyllabusUnit
from chalkboard.sources import (
    UNREADABLE,
    UploadError,
    data_dir,
    status_label,
    status_tone,
    store_upload,
)

MAX_UNITS = 60
TITLE_MAX = 200
LLM_TEXT_CHARS = 12000
NO_UNITS = "No units found"
PDF_ONLY = "Upload the syllabus as a PDF"

HEADING = re.compile(
    r"^\s*(unit|chapter)\s*[-–—:.]?\s*(\d{1,2}|[ivxlc]{1,7})\b\s*[-–—:.)]?\s*(.*?)\s*$",
    re.IGNORECASE,
)
TRAILING_COUNT = re.compile(
    r"(\s+\d+(\s*(periods?|marks?|hours?))?)+\s*$", re.IGNORECASE
)
ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
ROMAN_CANON = [(100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix")]
ROMAN_CANON += [(5, "v"), (4, "iv"), (1, "i")]

UNITS_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "title": {"type": "string"},
                },
                "required": ["number", "title"],
            },
        }
    },
    "required": ["units"],
}
UNITS_PROMPT = (
    "Below is the text of a school syllabus. List its units (or chapters) in the order they appear, "
    "with the unit number and its title. Use only what the text says. If it lists no units, return an "
    "empty list.\n\n{text}"
)


@dataclass(frozen=True)
class FoundUnit:
    number: int
    title: str


def int_to_roman(value: int) -> str:
    out = ""
    for size, letters in ROMAN_CANON:
        while value >= size:
            out += letters
            value -= size
    return out


def roman_to_int(text: str) -> int | None:
    text = text.lower()
    if not text or any(c not in ROMAN_VALUES for c in text):
        return None
    values = [ROMAN_VALUES[c] for c in text]
    total = 0
    for i, v in enumerate(values):
        total += -v if i + 1 < len(values) and v < values[i + 1] else v
    # Only the canonical spelling counts: "IIII" and "VX" read as numbers but aren't roman numerals.
    return total if total > 0 and int_to_roman(total) == text else None


def clean_title(raw: str) -> str:
    title = " ".join(raw.split()).strip(" :-–—.")
    return title[:TITLE_MAX].strip() if re.search(r"[^\W\d_]", title) else ""


def heading_units(text: str) -> list[FoundUnit]:
    lines = [line.strip() for line in text.splitlines()]
    # Units and chapters are kept apart so a chapter never takes a unit's number.
    found: dict[str, dict[int, str]] = {"unit": {}, "chapter": {}}
    for i, line in enumerate(lines):
        match = HEADING.match(line)
        if match is None:
            continue
        kind, number_text, title = match.groups()
        seen = found[kind.lower()]
        number = (
            int(number_text) if number_text.isdigit() else roman_to_int(number_text)
        )
        if not number:
            continue
        if not title:
            following = next((n for n in lines[i + 1 :] if n), "")
            title = "" if HEADING.match(following) else following
        title = clean_title(TRAILING_COUNT.sub("", title))
        if title and number not in seen:
            seen[number] = title
    best = next((f for f in (found["unit"], found["chapter"]) if len(f) >= 2), {})
    return [FoundUnit(n, t) for n, t in best.items()][:MAX_UNITS]


def parse_units(answer: object) -> list[FoundUnit]:
    items = answer.get("units") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return []
    found: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        number, title = item.get("number"), item.get("title")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number < 1
            or not isinstance(title, str)
        ):
            continue
        title = clean_title(title)
        if title and number not in found:
            found[number] = title
    return [FoundUnit(n, t) for n, t in found.items()][:MAX_UNITS]


def llm_units(client: LLMClient, text: str) -> list[FoundUnit]:
    if not text.strip():
        return []
    prompt = UNITS_PROMPT.format(text=text[:LLM_TEXT_CHARS])
    try:
        answer = client.generate(prompt, UNITS_SCHEMA)
    except FormatMismatch:
        return []
    return parse_units(answer)


def find_units(client: LLMClient, text: str) -> list[FoundUnit]:
    return heading_units(text) or llm_units(client, text)


def has_typed_units(session: Session, source_id: int) -> bool:
    return (
        session.scalar(
            select(SyllabusUnit.id)
            .where(SyllabusUnit.source_id == source_id, SyllabusUnit.typed)
            .limit(1)
        )
        is not None
    )


def replace_units(
    session: Session, source_id: int, units: list[FoundUnit], typed: bool
) -> None:
    # Everything goes, typed or not: callers check for typed units first when `typed` is False.
    session.execute(delete(SyllabusUnit).where(SyllabusUnit.source_id == source_id))
    session.add_all(
        SyllabusUnit(
            source_id=source_id,
            position=i,
            number=unit.number,
            title=unit.title,
            typed=typed,
        )
        for i, unit in enumerate(units)
    )


def unit_rows(session: Session, source_id: int) -> list[SyllabusUnit]:
    return list(
        session.scalars(
            select(SyllabusUnit)
            .where(SyllabusUnit.source_id == source_id)
            .order_by(SyllabusUnit.position)
        )
    )


@jobs.register("detect_units", needs_ollama=False)
def detect_units(ctx: JobContext) -> None:
    session = ctx.session
    source = session.get(Source, ctx.payload["source_id"])
    if source is None or not source.is_syllabus or has_typed_units(session, source.id):
        return
    ctx.progress("Reading units", 0)
    try:
        sections = extract(data_dir() / source.file_path, source.kind)
    except (ExtractError, OSError) as exc:
        raise jobs.JobFailed(UNREADABLE) from exc
    text = "\n".join(s.text for s in sections)
    subject = session.get(Subject, source.subject_id)
    client = model_client(ctx.teacher, subject)
    try:
        units = find_units(client, text)
    finally:
        client.close()
    # Lock and look again: the teacher may have typed units, or changed the syllabus, while the model ran.
    locked = session.scalar(
        select(Source)
        .where(Source.id == source.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or not locked.is_syllabus or has_typed_units(session, source.id):
        return
    replace_units(session, source.id, units, typed=False)


def current_syllabus(session: Session, subject: Subject) -> Source | None:
    return session.scalar(
        select(Source)
        .where(Source.subject_id == subject.id, Source.is_syllabus)
        .options(selectinload(Source.job))
        .execution_options(populate_existing=True)
    )


def use_as_syllabus(session: Session, subject: Subject, source: Source) -> None:
    try:
        # Serialises two marks at once.
        session.execute(
            select(Subject.id).where(Subject.id == subject.id).with_for_update()
        )
        # Look at the committed state: another request may have changed the flag or finished ingest meanwhile.
        locked = session.scalar(
            select(Source)
            .where(Source.id == source.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            return
        job = (
            session.scalar(
                select(Job)
                .where(Job.id == source.job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if source.job_id is not None
            else None
        )
        old = current_syllabus(session, subject)
        if old is source:
            return
        if old is not None:
            old.is_syllabus = False
            # Written before the new flag: two UPDATEs in one flush could trip the unique index.
            session.flush()
        source.is_syllabus = True
        session.flush()
        # A source still being read queues detection itself when it finishes.
        ready = job is None or job.state == "done"
        if ready and not has_typed_units(session, source.id):
            source.job = jobs.enqueue(
                session, subject.teacher_id, "detect_units", {"source_id": source.id}
            )
        session.commit()
    except BaseException:
        session.rollback()
        raise


def type_units(session: Session, source: Source, text: str) -> int:
    titles = [" ".join(line.split())[:TITLE_MAX] for line in text.splitlines()]
    titles = [t for t in titles if t][:MAX_UNITS]
    if not titles:
        raise ValueError("Type at least one unit, one per line")
    try:
        session.execute(
            select(Source.id).where(Source.id == source.id).with_for_update()
        )
        units = [FoundUnit(i + 1, t) for i, t in enumerate(titles)]
        replace_units(session, source.id, units, typed=True)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    return len(titles)


def syllabus_status(source: Source, unit_count: int) -> str:
    job = source.job
    if job is not None and job.state == "failed" and job.kind == "detect_units":
        # Reading the units failed; the file itself was read, so the teacher can type them.
        return (
            NO_UNITS
            if not unit_count
            else f"Ready · {unit_count} unit{'s' if unit_count != 1 else ''}"
        )
    if job is not None and job.state != "done":
        return status_label(source)
    if not unit_count:
        return NO_UNITS
    return f"Ready · {unit_count} unit{'s' if unit_count != 1 else ''}"


def syllabus_tone(source: Source, unit_count: int) -> str:
    if syllabus_status(source, unit_count) == NO_UNITS:
        return "chip-flag"
    return status_tone(source)


def unmark_syllabus(session: Session, subject: Subject) -> None:
    """The syllabus goes back to being a numbered source; its file, passages and units stay. The caller commits."""
    session.execute(
        select(Subject.id).where(Subject.id == subject.id).with_for_update()
    )
    old = current_syllabus(session, subject)
    if old is not None:
        old.is_syllabus = False
        session.flush()


def upload_syllabus(
    session: Session, subject: Subject, filename: str, stream: BinaryIO
) -> Source:
    if kind_for(filename) != "pdf":
        raise UploadError(PDF_ONLY)
    source = store_upload(session, subject, filename, stream)
    use_as_syllabus(session, subject, source)
    return source
