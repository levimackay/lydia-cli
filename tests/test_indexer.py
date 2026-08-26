"""Tests for chunking and incremental index building."""

from pathlib import Path

from lydia.context.indexer import (
    MAX_CHUNK_LINES,
    build_index,
    chunk_file,
)
from lydia.database import sqlite as db
from lydia.llm.client import OllamaConnectionError


class FakeEmbedClient:
    """Deterministic fake embeddings: vector = [len(text), call_count]."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        self.calls.append(list(inputs))
        return [[float(len(text)), float(i)] for i, text in enumerate(inputs)]


def test_chunk_empty_file_returns_nothing() -> None:
    assert chunk_file("a.py", "") == []


def test_chunk_small_file_is_one_chunk() -> None:
    text = "\n".join(f"line {i}" for i in range(10))
    chunks = chunk_file("a.py", text)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 10


def test_chunk_large_file_splits_with_overlap() -> None:
    text = "\n".join(f"line {i}" for i in range(200))
    chunks = chunk_file("a.py", text)
    assert len(chunks) > 1
    # consecutive chunks overlap: next start <= previous end
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start_line <= prev.end_line
    # every chunk stays within the file
    assert chunks[-1].end_line == 200


def test_chunk_snaps_to_blank_line_when_nearby() -> None:
    # blank line sits a few lines past the raw MAX_CHUNK_LINES cutoff, within lookahead range
    lines = [f"line {i}" for i in range(MAX_CHUNK_LINES + 3)] + ["", "next block starts here"]
    text = "\n".join(lines)
    chunks = chunk_file("a.py", text)
    # snapped forward to the blank line (line 63) rather than cut at the raw 60-line budget
    assert chunks[0].end_line == MAX_CHUNK_LINES + 3


def test_chunk_shares_file_hash_across_chunks() -> None:
    text = "\n".join(f"line {i}" for i in range(200))
    chunks = chunk_file("a.py", text)
    assert len({c.content_hash for c in chunks}) == 1


def test_build_index_embeds_new_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n")
    client = FakeEmbedClient()

    stats = build_index(tmp_path, client)

    assert stats.files_scanned == 2
    assert stats.files_indexed == 2
    conn = db.connect(tmp_path)
    assert db.indexed_paths(conn) == {"a.py", "b.py"}
    conn.close()


def test_build_index_skips_unchanged_files_on_rerun(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    client = FakeEmbedClient()
    build_index(tmp_path, client)

    client2 = FakeEmbedClient()
    stats = build_index(tmp_path, client2)

    assert stats.files_indexed == 0
    assert client2.calls == []  # never even asked to embed anything


def test_build_index_reembeds_changed_files(tmp_path: Path) -> None:
    file_path = tmp_path / "a.py"
    file_path.write_text("def foo():\n    return 1\n")
    client = FakeEmbedClient()
    build_index(tmp_path, client)

    file_path.write_text("def foo():\n    return 2\n")
    client2 = FakeEmbedClient()
    stats = build_index(tmp_path, client2)

    assert stats.files_indexed == 1
    assert client2.calls  # re-embedded


def test_build_index_removes_deleted_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    build_index(tmp_path, FakeEmbedClient())

    (tmp_path / "b.py").unlink()
    stats = build_index(tmp_path, FakeEmbedClient())

    assert stats.files_removed == 1
    conn = db.connect(tmp_path)
    assert db.indexed_paths(conn) == {"a.py"}
    conn.close()


def test_build_index_ignores_non_source_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("just some notes")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")
    stats = build_index(tmp_path, FakeEmbedClient())
    assert stats.files_scanned == 0


def test_build_index_ignores_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("var x = 1;")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    stats = build_index(tmp_path, FakeEmbedClient())
    assert stats.files_scanned == 1


def test_build_index_force_reembeds_unchanged_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    build_index(tmp_path, FakeEmbedClient())

    client2 = FakeEmbedClient()
    stats = build_index(tmp_path, client2, force=True)
    assert stats.files_indexed == 1
    assert client2.calls


class UnreachableEmbedClient:
    """Ollama is down: every embed call fails the same way."""

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        self.calls += 1
        raise OllamaConnectionError("http://localhost:11434")


def test_build_index_stops_at_first_embedding_failure_and_keeps_the_index(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    build_index(tmp_path, FakeEmbedClient())
    (tmp_path / "a.py").write_text("x = 2\n")  # both now need re-embedding
    (tmp_path / "b.py").write_text("y = 3\n")

    client = UnreachableEmbedClient()
    stats = build_index(tmp_path, client)

    assert client.calls == 1  # no per-file retry against a dead daemon
    assert stats.files_indexed == 0
    assert stats.error and "Cannot reach Ollama" in stats.error
    conn = db.connect(tmp_path)
    assert db.indexed_paths(conn) == {"a.py", "b.py"}  # unreached files not swept as stale
    conn.close()
