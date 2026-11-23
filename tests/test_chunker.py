import pytest

from chalkboard.chunker import chunk
from chalkboard.extract import Section


def words(n, start=0):
    return " ".join(f"w{i}" for i in range(start, start + n))


def test_short_text_is_one_piece():
    pieces = chunk([Section(words(10), None)])
    assert len(pieces) == 1
    assert (pieces[0].position, pieces[0].text) == (0, words(10))


def test_windows_overlap():
    pieces = chunk([Section(words(20), None)], size=10, overlap=3)
    assert [p.text.split()[0] for p in pieces] == ["w0", "w7", "w14"]
    assert pieces[1].text.split()[:3] == pieces[0].text.split()[-3:]
    assert [p.position for p in pieces] == [0, 1, 2]


def test_pages_tracked_across_sections():
    sections = [Section(words(6, i * 6), i + 1) for i in range(3)]
    pieces = chunk(sections, size=8, overlap=2)
    assert [(p.page_start, p.page_end) for p in pieces] == [(1, 2), (2, 3), (3, 3)]


def test_no_pages_for_plain_text():
    (piece,) = chunk([Section(words(5), None)])
    assert (piece.page_start, piece.page_end) == (None, None)


@pytest.mark.parametrize(
    "sections", [[], [Section("", None)], [Section("  \n", 1)]], ids=str
)
def test_empty_sections_give_no_pieces(sections):
    assert chunk(sections) == []


@pytest.mark.parametrize("overlap", [-1, 10, 11])
def test_bad_overlap_raises(overlap):
    with pytest.raises(ValueError):
        chunk([Section("a b", None)], size=10, overlap=overlap)
