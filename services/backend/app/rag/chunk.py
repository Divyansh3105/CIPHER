"""Splitting extracted pages into embeddable chunks (Phase 5).

The blueprint (docs/architecture.md Section 7) specifies ~500-800 token
chunks with ~15% overlap. This implements that with two deliberate
departures, both because the specified version does not survive contact with
real documents:

1. **Characters, not tokens.** Counting real tokens would mean shipping a
   tokeniser for a model whose tokeniser is not public
   (gemini-embedding-001). ~4 characters per token is the standard
   approximation for English prose, so the window is expressed in characters
   and named as an approximation rather than pretending to a precision it
   does not have.

2. **Split on structure first, length second.** Cutting purely by length
   routinely severs a sentence mid-clause, and the resulting chunk embeds
   badly *and* reads badly when cited. Paragraphs are preferred, then
   sentences, and only a single paragraph longer than the whole window is cut
   mid-text.

Overlap exists so a passage spanning a boundary is still retrievable whole
from one side or the other. Without it, the answer to a question that
straddles two chunks is in neither.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.extract import Page

#: ~650 tokens at the usual 4-chars-per-token approximation, inside the
#: blueprint's 500-800 range.
CHUNK_CHARS = 2600

#: ~15% of CHUNK_CHARS, per the blueprint.
CHUNK_OVERLAP_CHARS = 400

#: Below this a chunk carries too little context to be a useful citation --
#: a stray heading or page number on its own line, usually. Merged into its
#: neighbour rather than stored as its own retrievable passage.
MIN_CHUNK_CHARS = 120

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    content: str
    index: int
    page_number: int | None


def _split_long(text: str, limit: int) -> list[str]:
    """Break a single over-long block on sentence boundaries, then hard."""
    sentences = _SENTENCE_END.split(text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + 1 + len(sentence) > limit:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)

    # A single sentence longer than the window (tables, minified text, a
    # paragraph with no punctuation) still has to be cut somewhere.
    final: list[str] = []
    for part in parts:
        while len(part) > limit:
            final.append(part[:limit])
            part = part[limit:]
        if part:
            final.append(part)
    return final


def _blocks(text: str, limit: int) -> list[str]:
    blocks: list[str] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        if len(paragraph) > limit:
            blocks.extend(_split_long(paragraph, limit))
        else:
            blocks.append(paragraph)
    return blocks


def chunk_pages(
    pages: list[Page],
    *,
    chunk_chars: int = CHUNK_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[Chunk]:
    """Split pages into overlapping chunks, each tagged with its page.

    Chunks never span pages. That costs a little packing efficiency and buys
    the thing the citation is for: every chunk can name exactly one page, so
    "page 4" is always true rather than usually true.
    """
    chunks: list[Chunk] = []
    index = 0

    for page in pages:
        blocks = _blocks(page.text, chunk_chars)
        if not blocks:
            continue

        current = ""
        for block in blocks:
            if current and len(current) + 1 + len(block) > chunk_chars:
                chunks.append(Chunk(content=current, index=index, page_number=page.number))
                index += 1
                # Carry the tail forward so a passage straddling the boundary
                # survives intact in the next chunk.
                tail = current[-overlap_chars:] if overlap_chars > 0 else ""
                current = f"{tail} {block}".strip() if tail else block
            else:
                current = f"{current} {block}".strip() if current else block

        if current:
            # Too small to stand alone, and there is something to fold it
            # into: a lone heading is not a passage worth citing.
            if len(current) < MIN_CHUNK_CHARS and chunks and chunks[-1].page_number == page.number:
                previous = chunks.pop()
                index -= 1
                current = f"{previous.content} {current}".strip()
            chunks.append(Chunk(content=current, index=index, page_number=page.number))
            index += 1

    return chunks
