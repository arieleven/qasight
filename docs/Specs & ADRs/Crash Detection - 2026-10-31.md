---
type: spec
status: current
created: 2026-10-31
updated: 2026-11-04
---

# Crash Detection - 2026-10-31

Part of the product work ([Specs & ADRs](README.md)).

## Context

A test that recurses forever, or native code that dies on a bad pointer, showed up as one more red test. The cause was only in the log or the trace, and a segfaulting stage gave no failing test at all. QASight already sees every engine stage's output (the executor streams it into `run.log`) and every stored test's message and trace, so it can name the cause instead.

Scope came from a user idea ("see if there is stuck overflow in the test"). Two options were weighed: (A) name stack overflows and crashes, (B) stop stages that print nothing for N seconds. A was chosen first: it is cheap, rarely wrong, and works for imported results too. B stays a possible opt-in follow-up (healthy suites are often quiet for minutes, so it risks false alarms).

## Decision

- **One table of signatures** in `qasight/engine/crash_signatures.py`: each runtime's own stack overflow line (Python, Java/Kotlin, JavaScript, Go, Rust, .NET, Ruby, AddressSanitizer), plus native crash lines (`Segmentation fault`, AddressSanitizer `SEGV`). Two kinds only: `stack_overflow` and `crash`. A stack overflow beats the segfault it often causes. Go's nil-pointer panic ("segmentation violation") is an ordinary panic and is not matched.
- **Engine stages:** a `CrashScanner` sits on the stage's output stream (line by line, never raises) and the exit status is checked for crash signals (`SIGSEGV`, `SIGBUS`, `SIGILL`, `SIGABRT`, `SIGFPE`, as `-N` or `128+N`) and the Windows stack overflow / access violation codes. The output wins over the status, since it says why. The result is `crash_kind` / `crash_detail` on `StageResult`, stored with the stage in the run record and `plan_result.json`, and in `stage_finished`.
- **Tests, engine or imported:** the failing test's message and trace go through the same table when results are parsed (`reporting/allure_cases.load_cases`) and the kind is stored in the `test_result` table's `crash` column (revision `0004`), so JUnit, Jest, go test, TRX and every other import get it with no importer change.
- **Only failures are flagged.** A passed stage or test is never classified, so a test that expects `RecursionError` stays green. A timed-out stage keeps what it printed but not its exit signal (we killed it).
- **Where it shows:** a red "Stack overflow" / "Crashed" badge on the run page's failing tests and stages (with the evidence line under the stage), `💥 stack overflow` in the PR comment, a `crash:` line in the CLI's stage panel, and `crash_detected=` in the AI failure summary's evidence.

## Consequences

- The stage fields live in the run record's JSON metadata. The test kind is a `test_result` column (revision `0004`); rows stored before it keep `""` and show no badge, while older runs first read from their snapshot get it.
- False positives are possible only where a failing test's output quotes one of these lines; the evidence line is always shown so a reader can tell.
- C/C++ without AddressSanitizer only ever reads as "Crashed": a segfault doesn't say whether the stack ran out.
- Not done: stuck/no-output detection (option B), stack dumps of a hung process, a per-cycle "crashes" trend.
- Symptoms and fixes: [Troubleshooting § Stack overflow or crash](../CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md#stack-overflow-or-crash-exit-1).
