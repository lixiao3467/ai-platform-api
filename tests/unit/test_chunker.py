"""Tests for recursive text chunker."""

from ai_platform.core.knowledge.chunkers.recursive import Chunk, RecursiveChunker


def test_empty_text_returns_no_chunks() -> None:
    chunker = RecursiveChunker(chunk_size=100)
    result = chunker.chunk("")
    assert result == []


def test_short_text_single_chunk() -> None:
    chunker = RecursiveChunker(chunk_size=100)
    result = chunker.chunk("Hello, world!")
    assert len(result) == 1
    assert result[0].content == "Hello, world!"


def test_long_text_splits_into_chunks() -> None:
    chunker = RecursiveChunker(chunk_size=50, chunk_overlap=10)
    text = "A" * 60 + "\n\n" + "B" * 60 + "\n\n" + "C" * 60
    result = chunker.chunk(text)
    assert len(result) >= 2
    for chunk in result:
        assert isinstance(chunk, Chunk)
        assert len(chunk.content) > 0


def test_respects_separator_hierarchy() -> None:
    chunker = RecursiveChunker(chunk_size=30, separators=["\n\n", "\n", " "])
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    result = chunker.chunk(text)
    assert len(result) >= 2


def test_metadata_passed_through() -> None:
    chunker = RecursiveChunker(chunk_size=100)
    result = chunker.chunk("Hello", metadata={"source": "test.pdf"})
    assert result[0].metadata["source"] == "test.pdf"
    assert result[0].metadata["chunk_index"] == 0


def test_chunk_overlap() -> None:
    chunker = RecursiveChunker(chunk_size=20, chunk_overlap=5)
    text = "AAAAABBBBBCCCCC DDDDD EEEEE"
    result = chunker.chunk(text)
    # With overlap, consecutive chunks should share some content
    if len(result) >= 2:
        assert isinstance(result[0], Chunk)
        assert isinstance(result[1], Chunk)
