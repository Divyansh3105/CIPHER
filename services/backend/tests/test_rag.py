"""Extraction and chunking: the parts of RAG that are pure functions.

Worth testing carefully because they are silent when wrong. A chunker that
severs sentences still produces chunks, still embeds them, and still returns
results -- the answers just quietly get worse, with nothing anywhere saying
so.
"""
import pytest

from app.rag.chunk import CHUNK_CHARS, MIN_CHUNK_CHARS, chunk_pages
from app.rag.extract import ExtractionError, Page, content_hash, extract


# --- extraction ---------------------------------------------------------


def test_plain_text_is_extracted_as_one_pageless_unit():
    pages = extract("notes.txt", b"hello world")

    assert len(pages) == 1
    assert pages[0].text == "hello world"
    # Not page 1: .txt has no pages, and inventing one would put a false
    # "page 1" into every citation from a text file.
    assert pages[0].number is None


@pytest.mark.parametrize("name", ["notes.md", "README.markdown", "notes.TXT"])
def test_text_like_extensions_are_accepted(name):
    assert extract(name, b"# Heading\n\nbody")[0].text.startswith("# Heading")


def test_utf16_is_decoded():
    assert "café" in extract("notes.txt", "café".encode("utf-16"))[0].text


def test_empty_file_is_refused():
    with pytest.raises(ExtractionError, match="empty"):
        extract("notes.txt", b"")


def test_oversized_file_is_refused_with_its_size():
    with pytest.raises(ExtractionError, match="MB"):
        extract("big.txt", b"x" * (11 * 1024 * 1024))


def test_unsupported_type_lists_what_is_supported():
    with pytest.raises(ExtractionError, match=r"\.pdf"):
        extract("photo.png", b"\x89PNG")


def test_legacy_doc_gets_its_own_message():
    """"Why did .docx work and .doc not" is an obvious question."""
    with pytest.raises(ExtractionError, match="Save it as .docx"):
        extract("old.doc", b"\xd0\xcf\x11\xe0")


def test_content_hash_ignores_whitespace_but_not_words():
    a = [Page(number=None, text="the  quick\nbrown fox")]
    b = [Page(number=None, text="the quick brown fox")]
    c = [Page(number=None, text="the quick brown cat")]

    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash(c)


# --- chunking -----------------------------------------------------------


def test_short_page_is_one_chunk():
    chunks = chunk_pages([Page(number=1, text="A short page.")])

    assert len(chunks) == 1
    assert chunks[0].page_number == 1
    assert chunks[0].index == 0


def test_chunks_never_span_pages():
    """The whole reason citations can say "page 4" and be right.

    Packing across a page boundary would be more efficient and would make
    every citation from that chunk a coin flip between two pages.
    """
    pages = [Page(number=n, text=f"Page {n} content. " * 300) for n in (1, 2, 3)]

    chunks = chunk_pages(pages)

    assert len({c.page_number for c in chunks}) == 3
    for chunk in chunks:
        page = chunk.page_number
        assert f"Page {page} content." in chunk.content
        for other in (1, 2, 3):
            if other != page:
                assert f"Page {other} content." not in chunk.content


def test_long_page_is_split_with_overlap():
    text = " ".join(f"Sentence number {i} about something." for i in range(600))

    chunks = chunk_pages([Page(number=1, text=text)])

    assert len(chunks) > 1
    # Overlap exists so a passage straddling a boundary is retrievable whole
    # from one side. Without it the answer to a straddling question is in
    # neither chunk.
    tail = chunks[0].content[-200:]
    assert any(word in chunks[1].content for word in tail.split()[-6:])


def test_chunks_respect_the_size_budget():
    text = " ".join(f"Word{i}" for i in range(20000))

    for chunk in chunk_pages([Page(number=1, text=text)]):
        # Overlap is prepended, so a chunk can exceed the window by up to the
        # overlap; anything beyond that means the splitter is not splitting.
        assert len(chunk.content) <= CHUNK_CHARS * 2


def test_a_single_unbroken_run_is_still_split():
    """Minified text, a table, a paragraph with no sentence endings."""
    chunks = chunk_pages([Page(number=1, text="x" * (CHUNK_CHARS * 3))])

    assert len(chunks) >= 3


def test_chunk_indexes_are_contiguous_across_pages():
    pages = [Page(number=n, text=f"Page {n}. " * 400) for n in (1, 2)]

    indexes = [c.index for c in chunk_pages(pages)]

    assert indexes == list(range(len(indexes)))


def test_a_tiny_trailing_fragment_is_merged_not_stored_alone():
    """A lone heading is not a passage worth citing."""
    body = "This is a proper paragraph of prose. " * 60
    pages = [Page(number=1, text=f"{body}\n\nAppendix")]

    chunks = chunk_pages(pages)

    assert all(len(c.content) >= MIN_CHUNK_CHARS for c in chunks)
    assert "Appendix" in chunks[-1].content


def test_empty_pages_produce_no_chunks():
    assert chunk_pages([Page(number=1, text="   \n\n  ")]) == []
