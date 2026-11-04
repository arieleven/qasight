"""Stack overflow and crash detection (qasight/engine/crash_signatures.py)."""

from __future__ import annotations

import signal
import sys

import pytest

from qasight.engine.crash_signatures import (
    CRASH,
    STACK_OVERFLOW,
    CrashScanner,
    crash_from_returncode,
    crash_in_text,
    label,
    stage_crash,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


# What each runtime really prints when its stack overflows.
@pytest.mark.parametrize(
    ("runtime", "text"),
    [
        (
            "Python",
            "RecursionError: maximum recursion depth exceeded while calling a Python object",
        ),
        ("Python", "RecursionError: maximum recursion depth exceeded in comparison"),
        ("Python", "Fatal Python error: Cannot recover from stack overflow."),
        ("Java/Kotlin", 'Exception in thread "main" java.lang.StackOverflowError'),
        ("Java/Kotlin", "java.lang.StackOverflowError\n\tat com.acme.Tree.depth(Tree.kt:12)"),
        (
            "JavaScript",
            "RangeError: Maximum call stack size exceeded\n    at walk (src/tree.js:4:3)",
        ),
        ("JavaScript", "InternalError: too much recursion"),
        (
            "Go",
            "runtime: goroutine stack exceeds 1000000000-byte limit\nfatal error: stack overflow",
        ),
        ("Go", "fatal error: stack overflow"),
        (
            "Rust",
            "thread 'tests::deep' has overflowed its stack\nfatal runtime error: stack overflow",
        ),
        (".NET", "Stack overflow.\n   at Acme.Tree.Depth()\n   at Acme.Tree.Depth()"),
        (".NET", "System.StackOverflowException: Exception of type ..."),
        ("Ruby", "SystemStackError: stack level too deep"),
        ("C/C++", "==4242==ERROR: AddressSanitizer: stack-overflow on address 0x7ffd"),
    ],
)
def test_each_runtime_stack_overflow_is_named(runtime: str, text: str) -> None:
    found = crash_in_text(text)

    assert found is not None
    assert found.kind == STACK_OVERFLOW
    assert found.runtime == runtime
    assert found.label == "Stack overflow"


@pytest.mark.parametrize(
    "text",
    [
        "Segmentation fault (core dumped)",
        "Fatal Python error: Segmentation fault",
        "==1==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000",
    ],
)
def test_native_crashes_are_crashes_not_stack_overflows(text: str) -> None:
    found = crash_in_text(text)

    assert found is not None
    assert found.kind == CRASH
    assert found.label == "Crashed"


@pytest.mark.parametrize(
    "text",
    [
        "AssertionError: assert 1 == 2",
        # Go's nil-pointer panic is an ordinary panic, not a crash we name.
        "panic: runtime error: invalid memory address or nil pointer dereference\n"
        "[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x4a]",
        "test_recursion_error_is_raised FAILED",
        "def test_stack_overflow_guard():",
        "",
    ],
)
def test_ordinary_failures_are_not_flagged(text: str) -> None:
    assert crash_in_text(text) is None


def test_stack_overflow_wins_over_the_segfault_it_caused() -> None:
    text = "Fatal Python error: Segmentation fault\nFatal Python error: Cannot recover from stack overflow."

    found = crash_in_text(text)

    assert found is not None and found.kind == STACK_OVERFLOW
    assert found.detail == "Fatal Python error: Cannot recover from stack overflow."


def test_describe_names_the_runtime_and_the_line() -> None:
    found = crash_in_text("RangeError: Maximum call stack size exceeded")

    assert found is not None
    assert found.describe() == (
        "Stack overflow (JavaScript): RangeError: Maximum call stack size exceeded"
    )
    assert label(None) == ""


@pytest.mark.parametrize(
    ("returncode", "kind", "detail"),
    [
        (-int(signal.SIGSEGV), CRASH, "SIGSEGV"),
        (128 + int(signal.SIGSEGV), CRASH, "SIGSEGV"),
        (-int(signal.SIGABRT), CRASH, "SIGABRT"),
        (0xC00000FD, STACK_OVERFLOW, "STATUS_STACK_OVERFLOW"),
        (-1073741571, STACK_OVERFLOW, "STATUS_STACK_OVERFLOW"),
        (-1073741819, CRASH, "access violation"),
    ],
)
def test_crash_exit_statuses(returncode: int, kind: str, detail: str) -> None:
    found = crash_from_returncode(returncode)

    assert found is not None
    assert found.kind == kind
    assert detail in found.detail


@pytest.mark.parametrize("returncode", [0, 1, 2, 4, 5, 124, 125, 127, -int(signal.SIGTERM), 130])
def test_ordinary_exit_statuses_are_not_crashes(returncode: int) -> None:
    assert crash_from_returncode(returncode) is None


def test_scanner_finds_a_line_split_across_chunks() -> None:
    scanner = CrashScanner()
    scanner.feed(b"collected 3 items\nRecursionError: maximum rec")
    assert scanner.found is None
    scanner.feed(b"ursion depth exceeded\nmore output\n")

    found = scanner.finish()

    assert found is not None and found.kind == STACK_OVERFLOW


def test_scanner_reads_the_last_unterminated_line_and_skips_testo_lines() -> None:
    scanner = CrashScanner()
    scanner.feed(b"[qasight] crash detection: Segmentation fault\n")
    scanner.feed(b"Segmentation fault")

    found = scanner.finish()

    assert found is not None and found.kind == CRASH
    assert found.detail == "Segmentation fault"


def test_scanner_upgrades_a_crash_to_the_stack_overflow_behind_it() -> None:
    scanner = CrashScanner()
    scanner.feed(b"Fatal Python error: Segmentation fault\n")
    scanner.feed(b"thread 'main' has overflowed its stack\n")

    found = scanner.finish()

    assert found is not None and found.kind == STACK_OVERFLOW
    assert found.runtime == "Rust"


def test_scanner_never_raises_on_odd_bytes() -> None:
    scanner = CrashScanner()
    scanner.feed(b"\xff\xfe\x00 garbage \x80\n" * 3)
    scanner.feed(b"x" * 100_000)

    assert scanner.finish() is None


def test_stage_crash_only_reports_failed_stages() -> None:
    overflow = crash_in_text("RecursionError: maximum recursion depth exceeded")

    assert stage_crash(overflow, returncode=0, timed_out=False) is None
    assert stage_crash(overflow, returncode=1, timed_out=False) == overflow
    # A timeout's own exit says nothing, but what the stage printed before still counts.
    assert stage_crash(None, returncode=124, timed_out=True) is None
    assert stage_crash(overflow, returncode=124, timed_out=True) == overflow
    by_signal = stage_crash(None, returncode=-int(signal.SIGSEGV), timed_out=False)
    assert by_signal is not None and by_signal.kind == CRASH
    # The output says why; the signal only says that it died.
    assert stage_crash(overflow, returncode=-int(signal.SIGSEGV), timed_out=False) == overflow


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_real_segfault_exit_status_is_a_crash() -> None:
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"],
        capture_output=True,
        check=False,
    )

    found = crash_from_returncode(proc.returncode)
    assert found is not None and "SIGSEGV" in found.detail
