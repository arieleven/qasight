"""Execution events: writing NDJSON lines for a run and streaming them as SSE.

The worker writes every event of a run into ``artifacts/<cycle>/events.ndjson``
(the engine does most of it, `NdjsonCycleListener` adds the trigger verdict).
Viewers follow a run by tailing that file from the byte offset where it started.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from testo_core.config.schema import Plan
from testo_core.config.triggers import TriggerResult

if TYPE_CHECKING:
    from testo_api.cycle_execution_manager import CycleExecutionManager


class NdjsonCycleListener:
    """Writes the trigger verdict into the cycle's events.ndjson so the SSE stream carries it."""

    def __init__(self, events_path: Path, *, ci: bool) -> None:
        self._events_path = events_path
        self._ci = ci

    def trigger_evaluated(self, plan: Plan, result: TriggerResult) -> None:
        if not self._ci:
            return
        append_ndjson_line(
            self._events_path,
            {
                "event": "cycle_trigger",
                "cycle": plan.name,
                "status": "activated" if result.stimulus else "resting",
                "reason": result.reason,
                "matched": list(result.matched_paths),
                "mode": result.mode,
            },
        )

    def trigger_bypassed(self, plan: Plan) -> None:
        return

    def report_archived(self, report_id: UUID | None, *, background: bool) -> None:
        return


def append_ndjson_line(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))
        fh.write("\n")
        fh.flush()


def _sse_message(payload: dict[str, object]) -> str:
    event_name = str(payload.get("event") or "unknown")
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return f"event: {event_name}\ndata: {data}\n\n"


def _read_ndjson(path: Path, offset: int, end: int | None) -> tuple[list[dict[str, object]], int]:
    """Parse the NDJSON objects after byte ``offset``; return them and the new offset.

    While the run is live (``end is None``) a trailing line without its newline is
    still being written, so it is left for the next read. Once the run is done, read
    exactly up to ``end`` (the file is shared by later runs of the same cycle).
    """
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read() if end is None else fh.read(max(0, end - offset))
    except OSError:  # not created yet
        return [], offset if end is None else end
    consumed = chunk.rfind(b"\n") + 1 if end is None else len(chunk)
    payloads: list[dict[str, object]] = []
    for raw in chunk[:consumed].splitlines():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads, offset + consumed if end is None else end


async def iter_sse_from_ndjson_file(
    *,
    events_path: Path,
    start_offset_bytes: int,
    end_offset: Callable[[], int | None],
    min_poll_s: float = 0.1,
    max_poll_s: float = 2.0,
    keepalive_s: float = 15.0,
) -> AsyncIterator[str]:
    """
    Tail an NDJSON file and emit each JSON object as an SSE message.

    The `data:` payload is the full NDJSON object (including top-level `event`),
    aligned to `docs/CLI Commands/Troubleshooting and Error Codes.md`.

    `end_offset()` is None while the execution runs and, once it is done, the byte
    offset where its events end; the stream drains up to there and closes.

    Per viewer this is one coroutine and a file offset. It sleeps on the event loop
    (a sync generator would hold a threadpool thread per viewer) and the poll interval
    backs off from `min_poll_s` to `max_poll_s` while nothing new is written. When
    the client disconnects, Starlette cancels the response task, which ends the
    stream at its next `await`.
    """
    offset = max(0, int(start_offset_bytes))
    interval = min_poll_s
    last_sent = time.monotonic()
    while True:
        # Sample the end before reading: it is recorded after the worker's last event,
        # so this read is guaranteed to include that event.
        end = end_offset()
        # Off the event loop: a late viewer may have a large backlog to read.
        payloads, offset = await asyncio.to_thread(_read_ndjson, events_path, offset, end)
        for payload in payloads:
            yield _sse_message(payload)
        if end is not None:
            return

        now = time.monotonic()
        if payloads:
            interval = min_poll_s
            last_sent = now
        else:
            interval = min(interval * 2, max_poll_s)
            if now - last_sent >= keepalive_s:
                # Comment line: keeps proxies from timing out an idle stream.
                yield ": keep-alive\n\n"
                last_sent = now
        await asyncio.sleep(interval)


async def iter_execution_sse(
    manager: CycleExecutionManager,
    execution_id: str,
    *,
    min_poll_s: float = 0.1,
    max_poll_s: float = 2.0,
) -> AsyncIterator[str]:
    """SSE stream for one execution: wait until its events file is known, then tail it."""
    # The worker thread learns the events path only after loading the config.
    interval = min_poll_s
    while True:
        state = manager.get(execution_id)
        if state is None:  # evicted: it finished long ago and its result is in run history
            return
        with state.lock:
            events_path, start_offset = state.events_path, state.events_start_offset_bytes
            done, error, status = state.done, state.error, state.status
        if events_path is not None:
            break
        if done:
            # Failed before it had an events file (a config error), or stopped while queued.
            message = error or "execution finished without events"
            code = "cancelled" if status == "cancelled" else "internal_error"
            yield _sse_message({"event": "error", "code": code, "message": message})
            return
        await asyncio.sleep(interval)
        interval = min(interval * 2, max_poll_s)

    def end_offset() -> int | None:
        current = manager.get(execution_id)
        if current is None:  # evicted mid-stream: nothing more of this run to send
            return 0
        with current.lock:
            return (current.events_end_offset_bytes or 0) if current.done else None

    async for message in iter_sse_from_ndjson_file(
        events_path=events_path,
        start_offset_bytes=start_offset,
        end_offset=end_offset,
        min_poll_s=min_poll_s,
        max_poll_s=max_poll_s,
    ):
        yield message
