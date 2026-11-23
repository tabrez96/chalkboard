import docx
import pytest

from chalkboard.extract import ExtractError, Section, extract, kind_for
from tests.pdfs import make_pdf


def test_pdf_pages_numbered(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_bytes(make_pdf(["Alpha one", "Beta two"]))
    sections = extract(path, "pdf")
    assert [s.page for s in sections] == [1, 2]
    assert "Alpha" in sections[0].text
    assert "Beta" in sections[1].text


def test_docx_paragraphs_and_tables(tmp_path):
    path = tmp_path / "a.docx"
    document = docx.Document()
    document.add_paragraph("Photosynthesis basics")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Chlorophyll"
    document.save(str(path))
    (section,) = extract(path, "docx")
    assert "Photosynthesis basics" in section.text
    assert "Chlorophyll" in section.text
    assert section.page is None


def test_docx_keeps_paragraph_and_table_order(tmp_path):
    path = tmp_path / "a.docx"
    document = docx.Document()
    document.add_paragraph("Alpha")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Bravo"
    document.add_paragraph("Charlie")
    document.save(str(path))
    (section,) = extract(path, "docx")
    assert section.text.split("\n") == ["Alpha", "Bravo", "Charlie"]


def test_docx_reads_nested_tables(tmp_path):
    path = tmp_path / "a.docx"
    document = docx.Document()
    outer = document.add_table(rows=1, cols=1).cell(0, 0)
    outer.paragraphs[0].text = "Outer"
    outer.add_table(rows=1, cols=1).cell(0, 0).text = "Inner"
    document.save(str(path))
    (section,) = extract(path, "docx")
    assert [t for t in section.text.split("\n") if t] == ["Outer", "Inner"]


def test_txt_utf8_and_cp1252(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"\xef\xbb\xbfhello")
    assert extract(path, "txt") == [Section("hello", None)]
    path.write_bytes(b"caf\xe9")
    assert extract(path, "txt") == [Section("café", None)]


def test_md_kept_as_text(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("# Title\n\n* item\n")
    assert extract(path, "md") == [Section("# Title\n\n* item\n", None)]


@pytest.mark.parametrize("kind", ["pdf", "docx"])
def test_corrupt_files_raise_extract_error(tmp_path, kind):
    path = tmp_path / f"a.{kind}"
    path.write_bytes(b"\x00\x01garbage" * 50)
    with pytest.raises(ExtractError):
        extract(path, kind)


def test_empty_files_give_empty_text(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"")
    assert extract(path, "txt") == [Section("", None)]


def test_kind_for():
    assert kind_for("A.PDF") == "pdf"
    assert kind_for("x.doc") is None
    assert kind_for("noext") is None
