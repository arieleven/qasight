"""Unit tests for :mod:`qasight.engine.log_buffer` (QA Strategies ST rows).

Covers the file tee, the bounded ring buffer, tail slicing, the on_chunk
heartbeat callback, and the env-merge helper used by the executor.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from qasight.engine.log_buffer import LogBuffer, drain_stream_into_buffer, merged_env

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _buffer(tmp_path: Path, **kwargs: object) -> LogBuffer:
    return LogBuffer(log_path=tmp_path / "stage" / "run.log", **kwargs)  # type: ignore[arg-type]


def test_feed_tees_to_log_file_and_ring(tmp_path: Path) -> None:
    with _buffer(tmp_path) as buf:
        buf.feed(b"line-1\n")
        buf.feed(b"line-2\n")
        assert buf.tail() == "line-1\nline-2\n"
    # The parent dir is created and every chunk lands on disk (durable log).
    assert (tmp_path / "stage" / "run.log").read_bytes() == b"line-1\nline-2\n"


def test_ring_buffer_evicts_oldest_chunks_but_file_keeps_everything(tmp_path: Path) -> None:
    with _buffer(tmp_path, ring_bytes=8) as buf:
        buf.feed(b"AAAA")
        buf.feed(b"BBBB")
        buf.feed(b"CCCC")
        # Capacity 8: "AAAA" was evicted, tail keeps the newest bytes only.
        assert buf.tail() == "BBBBCCCC"
    assert (tmp_path / "stage" / "run.log").read_bytes() == b"AAAABBBBCCCC"


def test_tail_max_lines_and_max_bytes(tmp_path: Path) -> None:
    with _buffer(tmp_path) as buf:
        buf.feed(b"one\ntwo\nthree\nfour\n")
        assert buf.tail(max_lines=2) == "three\nfour"
        assert buf.tail(max_bytes=5) == "four\n"


def test_tail_decodes_invalid_utf8_with_replacement(tmp_path: Path) -> None:
    with _buffer(tmp_path) as buf:
        buf.feed(b"ok \xff\xfe bytes")
        text = buf.tail()
    assert "ok" in text and "�" in text


def test_empty_chunk_is_ignored_and_feed_after_close_is_noop(tmp_path: Path) -> None:
    buf = _buffer(tmp_path)
    buf.feed(b"")
    assert buf.tail() == ""
    buf.close()
    buf.close()  # idempotent
    buf.feed(b"late")  # must not raise or write
    assert (tmp_path / "stage" / "run.log").read_bytes() == b""


def test_on_chunk_callback_receives_every_chunk_and_may_crash(tmp_path: Path) -> None:
    seen: list[bytes] = []

    def cb(chunk: bytes) -> None:
        seen.append(chunk)
        raise RuntimeError("renderer bug must never kill the run")

    with _buffer(tmp_path, on_chunk=cb) as buf:
        buf.feed(b"a")
        buf.feed(b"b")
    assert seen == [b"a", b"b"]


def test_drain_stream_into_buffer_reads_until_eof(tmp_path: Path) -> None:
    stream = io.BytesIO(b"x" * 10_000)
    with _buffer(tmp_path) as buf:
        drain_stream_into_buffer(stream, buf, chunk_bytes=4096)
        assert buf.tail() == "x" * 10_000
    assert (tmp_path / "stage" / "run.log").stat().st_size == 10_000


def test_drain_stream_survives_closed_stream(tmp_path: Path) -> None:
    stream = io.BytesIO(b"data")
    stream.close()
    with _buffer(tmp_path) as buf:
        drain_stream_into_buffer(stream, buf)  # must swallow ValueError
        assert buf.tail() == ""


def test_merged_env_overlays_extra_pairs() -> None:
    parent = {"KEEP": "1", "OVERRIDE": "old"}
    out = merged_env(parent, [("OVERRIDE", "new"), ("ADDED", "2")])
    assert out == {"KEEP": "1", "OVERRIDE": "new", "ADDED": "2"}
    # Parent mapping is copied, never mutated.
    assert parent["OVERRIDE"] == "old"
    assert merged_env(parent, None) == parent


def test_on_chunk_failure_is_logged_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def cb(chunk: bytes) -> None:
        raise RuntimeError("renderer bug")

    with caplog.at_level("WARNING", logger="qasight.engine.log_buffer"):
        with _buffer(tmp_path, on_chunk=cb) as buf:
            buf.feed(b"a")
            buf.feed(b"b")

    failures = [r for r in caplog.records if "renderer failed" in r.getMessage()]
    assert len(failures) == 1
    assert failures[0].exc_info is not None
