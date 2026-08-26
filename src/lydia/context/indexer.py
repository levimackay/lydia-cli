"""Builds and incrementally updates the semantic search index (Milestone 2).

Chunks are language-agnostic "paragraph windows": a fixed line budget that
snaps forward to the next blank line within a short lookahead, so a chunk
boundary usually lands between functions/blocks rather than mid-statement,
without needing a real parser for every language Lydia might see.

Re-indexing only touches files whose content actually changed since the
last index, tracked by a hash of the file's text stored alongside each of
its chunks.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from lydia.context.scanner import IGNORED_DIRS, LANGUAGE_BY_EXTENSION
from lydia.database import sqlite as db
from lydia.llm.client import OllamaError
from lydia.llm.protocol import ModelClient

logger = logging.getLogger(__name__)

MAX_CHUNK_LINES = 60
LOOKAHEAD_LINES = 10  # how far past the budget we'll look for a blank line to snap to
OVERLAP_LINES = 3
MAX_FILE_BYTES = 300_000  # matches tools/filesystem's read cap; skip huge generated files
EMBED_MODEL = "nomic-embed-text"
EMBED_BATCH_SIZE = 32


@dataclass
class Chunk:
    path: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    text: str
    content_hash: str  # hash of the whole file this chunk came from


def chunk_file(relative_path: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    n = len(lines)
    chunks: list[Chunk] = []
    start = 0
    while start < n:
        end = min(start + MAX_CHUNK_LINES, n)
        if end < n:
            lookahead_limit = min(end + LOOKAHEAD_LINES, n)
            for candidate in range(end, lookahead_limit):
                if lines[candidate].strip() == "":
                    end = candidate
                    break
        text_slice = "\n".join(lines[start:end])
        if text_slice.strip():
            chunks.append(Chunk(
                path=relative_path, start_line=start + 1, end_line=end,
                text=text_slice, content_hash=file_hash,
            ))
        if end >= n:
            break
        start = max(end - OVERLAP_LINES, start + 1)
    return chunks


def _iter_source_files(root: Path):
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in IGNORED_DIRS and not entry.name.startswith(".") and not entry.is_symlink():
                    stack.append(entry)
                continue
            if entry.is_file() and entry.suffix.lower() in LANGUAGE_BY_EXTENSION:
                yield entry


@dataclass
class IndexStats:
    files_scanned: int = 0
    files_indexed: int = 0
    files_removed: int = 0
    chunks_indexed: int = 0
    error: str | None = None  # why indexing stopped early, if it did


def build_index(project_root: Path, client: ModelClient, force: bool = False) -> IndexStats:
    """Incrementally (re)build the semantic index for a project."""
    stats = IndexStats()
    conn = db.connect(project_root)
    try:
        existing_hashes: dict[str, str] = {} if force else db.file_hashes(conn)
        seen_paths: set[str] = set()

        for file_path in _iter_source_files(project_root):
            stats.files_scanned += 1
            relative = str(file_path.relative_to(project_root))
            seen_paths.add(relative)
            try:
                if file_path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if existing_hashes.get(relative) == current_hash:
                continue  # unchanged since the last index

            chunks = chunk_file(relative, text)
            if not chunks:
                continue
            try:
                embeddings = _embed_all(client, [c.text for c in chunks])
            except OllamaError as exc:
                # An embedding failure is never about one file (Ollama went
                # away, the embed model is missing), so retrying per file
                # only repeats the same error for every remaining file and
                # then reports "embedded 0 files" as if that were fine.
                logger.warning("Stopped indexing at %s: %s", relative, exc)
                stats.error = str(exc)
                break

            db.delete_path(conn, relative)
            db.insert_chunks(conn, chunks, embeddings)
            stats.files_indexed += 1
            stats.chunks_indexed += len(chunks)

        if stats.error is None:
            # Only after a complete walk: after an early stop, every file
            # not yet reached would look deleted and be swept from the index.
            for stale_path in db.indexed_paths(conn) - seen_paths:
                db.delete_path(conn, stale_path)
                stats.files_removed += 1

        conn.commit()
    finally:
        conn.close()
    return stats


def _embed_all(client: ModelClient, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        vectors.extend(client.embed(EMBED_MODEL, batch))
    return vectors
