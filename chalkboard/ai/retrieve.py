"""Retrieval: the subject's teaching passages closest to a query; the syllabus is never one of them."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from chalkboard.ai.base import Embedder
from chalkboard.models import Chunk, Source

TOP_K = 8


@dataclass(frozen=True)
class Passage:
    label: str  # "P1" … "Pn", in rank order
    chunk_id: int
    source_number: int
    text: str
    page_start: int | None
    page_end: int | None


def retrieve(
    session: Session, embedder: Embedder, subject_id: int, query: str, k: int = TOP_K
) -> list[Passage]:
    if not query.strip() or k < 1:
        return []
    vector = embedder.embed([query])[0]
    rows = session.execute(
        select(Chunk.id, Source.number, Chunk.text, Chunk.page_start, Chunk.page_end)
        .join(Source, Source.id == Chunk.source_id)
        # The syllabus decides chapters and order, but is never a teaching citation.
        .where(Source.subject_id == subject_id, Source.is_syllabus.is_(False))
        .order_by(Chunk.embedding.cosine_distance(vector), Chunk.id)
        .limit(k)
    ).all()
    return [Passage(f"P{i + 1}", *row) for i, row in enumerate(rows)]
