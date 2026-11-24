from chalkboard.ai.fake import FakeEmbedder
from chalkboard.models import Chunk, Source


def add_source(session, subject, number, texts, *, is_syllabus=False) -> list[Chunk]:
    source = Source(
        subject_id=subject.id,
        number=number,
        name=f"source-{number}.txt",
        kind="txt",
        file_path=f"sources/{number}.txt",
        is_syllabus=is_syllabus,
    )
    session.add(source)
    session.flush()
    embedder = FakeEmbedder()
    chunks = [
        Chunk(
            source_id=source.id,
            position=i,
            text=text,
            embedding=embedder.embed([text])[0],
        )
        for i, text in enumerate(texts)
    ]
    session.add_all(chunks)
    session.flush()
    return chunks
