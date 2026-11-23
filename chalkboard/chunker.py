"""Splits extracted text into overlapping passages that remember their pages."""

from dataclasses import dataclass

from chalkboard.extract import Section

# Words stand in for tokens: ~380 words is ~500 tokens for English prose.
CHUNK_WORDS = 380
OVERLAP_WORDS = 60


@dataclass(frozen=True)
class Piece:
    position: int
    text: str
    page_start: int | None
    page_end: int | None


def chunk(
    sections: list[Section], size: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS
) -> list[Piece]:
    if not 0 <= overlap < size:
        raise ValueError("overlap must be at least 0 and less than size")
    words = [(w, s.page) for s in sections for w in s.text.split()]
    pieces: list[Piece] = []
    start = 0
    while words:
        window = words[start : start + size]
        pieces.append(
            Piece(
                len(pieces), " ".join(w for w, _ in window), window[0][1], window[-1][1]
            )
        )
        if start + size >= len(words):
            break
        start += size - overlap
    return pieces
