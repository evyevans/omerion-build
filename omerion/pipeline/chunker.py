"""DocumentChunker — recursive character text splitter using tiktoken.

Chunk size: 512 tokens (cl100k_base), overlap: 50 tokens.
Never splits in the middle of a fenced code block.
"""
from __future__ import annotations

import hashlib
from typing import Any

import tiktoken

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("pipeline.chunker")

_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
_ENCODING_NAME = "cl100k_base"


def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding(_ENCODING_NAME)


def _token_len(text: str, enc: tiktoken.Encoding) -> int:
    return len(enc.encode(text))


def _protect_code_blocks(text: str) -> list[tuple[str, bool]]:
    """Split text into segments marking code blocks as non-splittable."""
    parts: list[tuple[str, bool]] = []
    in_block = False
    for segment in text.split("```"):
        parts.append((segment, in_block))
        in_block = not in_block
    return parts


def _recursive_split(text: str, separators: list[str], chunk_size: int, enc: tiktoken.Encoding) -> list[str]:
    """Split text recursively using the first separator that produces sub-chunks."""
    if _token_len(text, enc) <= chunk_size:
        return [text] if text.strip() else []

    for sep in separators:
        if sep == "" or sep in text:
            splits = text.split(sep) if sep else list(text)
            chunks: list[str] = []
            current = ""
            for part in splits:
                candidate = (current + sep + part) if current else part
                if _token_len(candidate, enc) <= chunk_size:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    # part itself may be too long — recurse
                    remaining_seps = separators[separators.index(sep) + 1:]
                    if _token_len(part, enc) > chunk_size and remaining_seps:
                        chunks.extend(_recursive_split(part, remaining_seps, chunk_size, enc))
                        current = ""
                    else:
                        current = part
            if current:
                chunks.append(current)
            return [c for c in chunks if c.strip()]

    return [text]


def _add_overlap(chunks: list[str], overlap: int, enc: tiktoken.Encoding) -> list[str]:
    """Prepend the tail of the previous chunk as overlap context."""
    if overlap <= 0 or len(chunks) < 2:
        return chunks
    result = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tokens = enc.encode(chunks[i - 1])
        overlap_tokens = prev_tokens[-overlap:] if len(prev_tokens) > overlap else prev_tokens
        overlap_text = enc.decode(overlap_tokens)
        result.append(overlap_text + " " + chunks[i])
    return result


class DocumentChunker:
    """Split a document into overlapping token-bounded chunks with metadata."""

    def chunk(self, text: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Return list of chunk dicts, each with 'text' and 'metadata' fields."""
        chunk_size: int = settings.chunk_size
        chunk_overlap: int = settings.chunk_overlap
        enc = _encoder()

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        # Preserve code blocks intact — split non-code portions, keep code portions whole
        segments = _protect_code_blocks(text)
        raw_chunks: list[str] = []
        for segment_text, is_code in segments:
            if not segment_text.strip():
                continue
            if is_code:
                # Wrap back in fences and treat as atomic chunk
                fenced = f"```{segment_text}```"
                raw_chunks.append(fenced)
            else:
                raw_chunks.extend(_recursive_split(segment_text, _SEPARATORS, chunk_size, enc))

        # Re-merge tiny adjacent non-code chunks up to chunk_size before adding overlap
        merged: list[str] = []
        current = ""
        for chunk in raw_chunks:
            candidate = (current + "\n\n" + chunk).strip() if current else chunk
            if _token_len(candidate, enc) <= chunk_size:
                current = candidate
            else:
                if current:
                    merged.append(current)
                current = chunk
        if current:
            merged.append(current)

        overlapped = _add_overlap(merged, chunk_overlap, enc)
        total = len(overlapped)

        result: list[dict[str, Any]] = []
        for idx, chunk_text in enumerate(overlapped):
            if not chunk_text.strip():
                continue
            result.append({
                "text": chunk_text.strip(),
                "metadata": {
                    **metadata,
                    "chunk_index": idx,
                    "total_chunks": total,
                    "content_hash": content_hash,
                },
            })

        log.info("chunker_complete", file_id=metadata.get("file_id"), chunks=len(result))
        return result
