"""Spot stack overflows and hard crashes in test output.

A test that recurses forever, or native code that dies on a bad pointer, fails
in a way that reads like any other red test unless someone opens the log. Each
runtime prints a very specific line when its stack overflows, so a short table
of patterns is enough to name the cause:

* :class:`CrashScanner` reads an engine stage's live output (the executor feeds
  it every chunk it writes to ``run.log``).
* :func:`crash_in_text` classifies a failing test's message and trace, which
  works for imported results (JUnit, Jest, go test, TRX, ...) as well.
* :func:`crash_from_returncode` names a process killed by a crash signal
  (``SIGSEGV`` and friends, or the Windows stack-overflow status).

Only failures are classified: a test that expects ``RecursionError`` and passes
is never flagged. See ``docs/Specs & ADRs/Crash Detection - 2026-10-31.md``.
"""

from __future__ import annotations

import logging
import re
import signal
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

CrashKind = Literal["stack_overflow", "crash"]

STACK_OVERFLOW: CrashKind = "stack_overflow"
CRASH: CrashKind = "crash"

# Longest line kept as evidence, and longest line scanned (a minified bundle on one
# line shouldn't cost a regex pass over megabytes).
_DETAIL_MAX = 300
_LINE_SCAN_MAX = 4000
# Bytes of an unterminated line kept between chunks.
_CARRY_MAX = 8192
# Characters of a test's message + trace looked at.
_TEXT_SCAN_MAX = 64_000


@dataclass(frozen=True)
class Signature:
    kind: CrashKind
    runtime: str
    pattern: re.Pattern[str]


def _sig(kind: CrashKind, runtime: str, pattern: str) -> Signature:
    return Signature(kind, runtime, re.compile(pattern))


# Most specific first. A stack overflow always wins over a plain crash, because
# native stack exhaustion often prints both ("stack overflow" then "Segmentation fault").
SIGNATURES: tuple[Signature, ...] = (
    _sig(STACK_OVERFLOW, "Python", r"RecursionError: maximum recursion depth exceeded"),
    _sig(STACK_OVERFLOW, "Python", r"Fatal Python error: Cannot recover from stack overflow"),
    _sig(STACK_OVERFLOW, "Java/Kotlin", r"java\.lang\.StackOverflowError"),
    _sig(STACK_OVERFLOW, "JavaScript", r"RangeError: Maximum call stack size exceeded"),
    _sig(STACK_OVERFLOW, "JavaScript", r"InternalError: too much recursion"),
    _sig(STACK_OVERFLOW, "Go", r"goroutine stack exceeds \d+-byte limit"),
    _sig(STACK_OVERFLOW, "Rust", r"thread '[^']*' has overflowed its stack"),
    _sig(STACK_OVERFLOW, "Rust", r"fatal runtime error: stack overflow"),
    _sig(STACK_OVERFLOW, "Go", r"^fatal error: stack overflow"),
    _sig(STACK_OVERFLOW, ".NET", r"System\.StackOverflowException|^Stack overflow\.\s*$"),
    _sig(STACK_OVERFLOW, "Ruby", r"SystemStackError|stack level too deep"),
    _sig(STACK_OVERFLOW, "C/C++", r"AddressSanitizer: stack-overflow"),
    # A crash, cause unknown. Go's nil-pointer panic says "segmentation violation",
    # which is an ordinary panic and deliberately not matched here.
    _sig(CRASH, "C/C++", r"AddressSanitizer: (?:SEGV|stack-buffer-overflow|heap-buffer-overflow)"),
    _sig(CRASH, "", r"Segmentation fault|Bus error \(core dumped\)"),
    _sig(CRASH, "Python", r"Fatal Python error: (?:Segmentation fault|Aborted|Bus error)"),
)

_ANY = re.compile("|".join(f"(?:{s.pattern.pattern})" for s in SIGNATURES), re.MULTILINE)

_CRASH_SIGNALS = {
    getattr(signal, name): name
    for name in ("SIGSEGV", "SIGBUS", "SIGILL", "SIGABRT", "SIGFPE")
    if hasattr(signal, name)
}
# NTSTATUS codes a Windows process exits with (unsigned, and as a signed 32-bit int).
_WINDOWS_STATUS: dict[int, tuple[CrashKind, str]] = {
    0xC00000FD: (STACK_OVERFLOW, "exit status 0xC00000FD (STATUS_STACK_OVERFLOW)"),
    0xC0000005: (CRASH, "exit status 0xC0000005 (access violation)"),
}

_LABELS: dict[str, str] = {STACK_OVERFLOW: "Stack overflow", CRASH: "Crashed"}


def label(kind: str | None) -> str:
    """Human label for a crash kind (``"Stack overflow"``, ``"Crashed"``)."""
    return _LABELS.get(kind or "", "")


@dataclass(frozen=True)
class Crash:
    kind: CrashKind
    # The runtime the pattern belongs to ("Python", "Go", ...), "" when unknown.
    runtime: str
    # The line (or exit status) that gave it away.
    detail: str

    @property
    def label(self) -> str:
        return label(self.kind)

    def describe(self) -> str:
        """One line for logs and summaries: ``Stack overflow (Python): RecursionError: ...``."""
        where = f" ({self.runtime})" if self.runtime else ""
        return f"{self.label}{where}: {self.detail}"


def _match_line(line: str) -> Crash | None:
    line = line[:_LINE_SCAN_MAX]
    if not _ANY.search(line):
        return None
    for sig in SIGNATURES:
        if sig.pattern.search(line):
            return Crash(sig.kind, sig.runtime, line.strip()[:_DETAIL_MAX])
    return None


def _prefer(current: Crash | None, found: Crash) -> Crash:
    if current is None or (current.kind == CRASH and found.kind == STACK_OVERFLOW):
        return found
    return current


def crash_in_text(text: str | None) -> Crash | None:
    """The crash a failure message or trace shows, stack overflows first; ``None`` if none."""
    if not text:
        return None
    text = text[:_TEXT_SCAN_MAX]
    if not _ANY.search(text):
        return None
    best: Crash | None = None
    for line in text.splitlines():
        found = _match_line(line)
        if found is not None:
            best = _prefer(best, found)
            if best.kind == STACK_OVERFLOW:
                break
    return best


def crash_from_returncode(returncode: int) -> Crash | None:
    """A crash told by the exit status alone: killed by a crash signal, or a Windows crash code."""
    rc = int(returncode)
    status = _WINDOWS_STATUS.get(rc & 0xFFFFFFFF) if rc > 255 or rc < -255 else None
    if status is not None:
        kind, detail = status
        return Crash(kind, "", detail)
    # A negative code is Python's "killed by signal N"; 128+N is the shell's (and Docker's).
    if rc < 0:
        name = _CRASH_SIGNALS.get(-rc)
    elif 128 < rc < 160:
        name = _CRASH_SIGNALS.get(rc - 128)
    else:
        name = None
    if name is None:
        return None
    return Crash(CRASH, "", f"process killed by {name} (exit {rc})")


class CrashScanner:
    """Line-by-line crash detection over a byte stream, fed one chunk at a time.

    Never raises: it rides on the stage's output stream, which must not break.
    """

    def __init__(self) -> None:
        self._carry = b""
        self._found: Crash | None = None
        self._failed = False

    @property
    def found(self) -> Crash | None:
        return self._found

    def feed(self, chunk: bytes) -> None:
        if not chunk or self._failed or self._done():
            return
        try:
            data = self._carry + chunk
            *lines, rest = data.split(b"\n")
            self._carry = rest[-_CARRY_MAX:]
            for raw in lines:
                self._scan(raw)
                if self._done():
                    self._carry = b""
                    return
        except Exception:
            # Broad on purpose: detection is a bonus; the stage's output must keep flowing.
            self._failed = True
            logger.warning("crash detection stopped for this stage", exc_info=True)

    def finish(self) -> Crash | None:
        """Scan the last unterminated line and return what was found."""
        if self._carry and not self._failed and not self._done():
            self._scan(self._carry)
        self._carry = b""
        return self._found

    def _done(self) -> bool:
        return self._found is not None and self._found.kind == STACK_OVERFLOW

    def _scan(self, raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace").rstrip("\r")
        if line.startswith(("[qasight]", "[testo]")):
            return
        found = _match_line(line)
        if found is not None:
            self._found = _prefer(self._found, found)


def stage_crash(found_in_output: Crash | None, *, returncode: int, timed_out: bool) -> Crash | None:
    """What to report for a finished stage: only a failed one that didn't simply time out.

    The output beats the exit status: it says *why* (a stack overflow killed by
    ``SIGSEGV`` is still a stack overflow).
    """
    if returncode == 0:
        return None
    if timed_out:
        # We killed it; its signal says nothing. What it printed before still counts.
        return found_in_output
    return found_in_output or crash_from_returncode(returncode)
