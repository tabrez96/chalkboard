"""Text extractors for uploaded files (pdf, docx, txt and md) and for saved web pages."""

import codecs
from dataclasses import dataclass
from pathlib import Path

import docx
import trafilatura
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from trafilatura.utils import decode_file


@dataclass(frozen=True)
class Section:
    text: str
    page: int | None  # 1-based PDF page; None when the format has no pages


class ExtractError(Exception): ...


KINDS = {".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "md"}


def kind_for(filename: str) -> str | None:
    return KINDS.get(Path(filename).suffix.lower())


def _pdf(path: Path) -> list[Section]:
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ExtractError("The PDF is password protected")
    return [
        Section(page.extract_text() or "", n)
        for n, page in enumerate(reader.pages, start=1)
    ]


def _docx_blocks(container) -> list[str]:
    """Paragraph and table-cell texts in document order, descending into nested tables."""
    parts: list[str] = []
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            parts.append(block.text)
        else:
            for row in block.rows:
                for cell in row.cells:
                    parts.extend(_docx_blocks(cell))
    return parts


def _docx(path: Path) -> list[Section]:
    document = docx.Document(str(path))
    return [Section("\n".join(_docx_blocks(document)), None)]


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _plain(path: Path) -> list[Section]:
    return [Section(_decode(path.read_bytes()), None)]


def _declared(page_body: bytes, charset: str | None) -> str | None:
    """The body decoded with the charset the server declared, or None when there is none or it is unknown."""
    if not charset:
        return None
    try:
        codecs.lookup(charset)
    except LookupError:
        return None
    return page_body.decode(charset, errors="replace")


def page_text(
    page_body: bytes, content_type: str, url: str, charset: str | None = None
) -> tuple[str, str]:
    """(title, main text) of a saved page; text is "" when nothing readable was found."""
    decoded = _declared(page_body, charset)
    if content_type == "text/plain":
        return "", decoded if decoded is not None else _decode(page_body)
    html = decoded if decoded is not None else decode_file(page_body)
    text = (
        trafilatura.extract(html, url=url, include_comments=False, include_tables=True)
        or ""
    )
    meta = trafilatura.extract_metadata(html)
    title = meta.title if meta and meta.title else ""
    return " ".join(title.split()), text


def extract(path: Path, kind: str) -> list[Section]:
    """Read a file's text. Raises ExtractError when it can't be read; an empty file is not an error."""
    try:
        if kind == "pdf":
            return _pdf(path)
        if kind == "docx":
            return _docx(path)
        return _plain(path)
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError(str(exc)) from exc
