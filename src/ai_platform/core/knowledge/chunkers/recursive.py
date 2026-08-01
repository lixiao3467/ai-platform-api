"""Recursive text chunker — default chunking strategy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    """A text chunk with metadata."""

    content: str
    metadata: dict = field(default_factory=dict)


class RecursiveChunker:
    """
    Recursive character-based text chunker.

    Splits text using a hierarchy of separators, keeping chunks
    within the target size while preserving semantic boundaries.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        separators: list[str] | None = None,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", "。", ".", " ", ""]

    def chunk(self, text: str, metadata: dict | None = None) -> list[Chunk]:
        """Split text into overlapping chunks."""
        if not text.strip():
            return []

        base_meta = metadata or {}
        raw_chunks = self._split_recursive(text, self.separators)

        # Merge small chunks and apply overlap
        merged = self._merge_chunks(raw_chunks)

        return [
            Chunk(content=chunk, metadata={**base_meta, "chunk_index": i})
            for i, chunk in enumerate(merged)
        ]

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        """Recursively split text using separator hierarchy."""
        if len(text) <= self.chunk_size:
            return [text.strip()] if text.strip() else []

        if not separators:
            # Force split at chunk_size boundary
            return [
                text[i : i + self.chunk_size].strip()
                for i in range(0, len(text), self.chunk_size - self.chunk_overlap)
                if text[i : i + self.chunk_size].strip()
            ]

        separator = separators[0]
        remaining_separators = separators[1:]

        if separator == "":
            return self._split_recursive(text, remaining_separators)

        parts = text.split(separator)

        chunks = []
        current = ""

        for part in parts:
            candidate = current + separator + part if current else part

            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                if current.strip():
                    chunks.append(current.strip())
                # If single part exceeds size, recurse with next separator
                if len(part) > self.chunk_size:
                    sub_chunks = self._split_recursive(part, remaining_separators)
                    chunks.extend(sub_chunks)
                    current = ""
                else:
                    current = part

        if current.strip():
            chunks.append(current.strip())

        return chunks

    def _merge_chunks(self, chunks: list[str]) -> list[str]:
        """Merge small consecutive chunks and apply overlap."""
        if not chunks:
            return []

        merged = []
        current = chunks[0]

        for chunk in chunks[1:]:
            if len(current) + len(chunk) + 1 <= self.chunk_size:
                current = current + "\n" + chunk
            else:
                merged.append(current)
                # Apply overlap: take last N chars of current as prefix
                if self.chunk_overlap > 0 and len(current) > self.chunk_overlap:
                    overlap_text = current[-self.chunk_overlap :]
                    current = overlap_text + "\n" + chunk
                else:
                    current = chunk

        merged.append(current)
        return merged
