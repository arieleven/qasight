"""Jest and Vitest adapters — ``npx jest`` / ``npx vitest run`` with results QASight can read.

The stage's ``args`` are passed to the runner (test paths, ``-t``, ``--project``,
``--config``...). The adapter adds what a ``command`` stage would need by hand:

- the runner's own JSON report, which needs no extra package
  (``--json --outputFile=...`` for Jest, ``--reporter=default --reporter=json
  --outputFile.json=...`` for Vitest), written to
  ``<target_repo>/test-results/testo-jest.json`` / ``testo-vitest.json`` and
  imported after the stage (``default_junit_xml``) by
  :mod:`qasight.reporting.jest_import`. A stage that picks its own reporters
  (``--json``/``--outputFile`` for Jest, ``--reporter`` for Vitest) keeps them;
  set ``junit_xml`` to the file they write.
- ``--maxWorkers=N`` from the stage's ``workers`` (unless ``args`` set it).
- Vitest runs once (``vitest run``), never in watch mode.

Jest or Vitest itself must be installed in the target project.
"""

from __future__ import annotations

from pathlib import Path

from qasight.frameworks.base import NativeReport

JEST_JSON_RELPATH = "test-results/testo-jest.json"
VITEST_JSON_RELPATH = "test-results/testo-vitest.json"


def has_flag(args: tuple[str, ...], *flags: str) -> bool:
    """True when ``args`` sets any of ``flags`` (``--flag``, ``--flag=x`` or ``--flag.sub=x``)."""
    return any(
        a in flags or a.startswith(tuple(f"{f}{sep}" for f in flags for sep in "=.")) for a in args
    )


class JestAdapter:
    name: str = "jest"
    default_junit_xml: tuple[str, ...] = (JEST_JSON_RELPATH,)

    def results_subdir(self) -> str:
        return "jest"

    def build_argv(
        self,
        *,
        target_repo: Path,
        results_dir: Path,
        stage_args: tuple[str, ...],
        workers: int,
    ) -> list[str]:
        del results_dir
        argv = ["npx", "jest", *stage_args]
        if not has_flag(stage_args, "--json", "--outputFile"):
            out = target_repo.expanduser().resolve() / JEST_JSON_RELPATH
            argv += ["--json", f"--outputFile={out}"]
        if workers > 0 and not has_flag(stage_args, "--maxWorkers", "-w", "--runInBand", "-i"):
            argv.append(f"--maxWorkers={workers}")
        return argv

    def native_report(self, stage_dir: Path) -> NativeReport | None:
        del stage_dir
        return None


class VitestAdapter:
    name: str = "vitest"
    default_junit_xml: tuple[str, ...] = (VITEST_JSON_RELPATH,)

    def results_subdir(self) -> str:
        return "vitest"

    def build_argv(
        self,
        *,
        target_repo: Path,
        results_dir: Path,
        stage_args: tuple[str, ...],
        workers: int,
    ) -> list[str]:
        del results_dir
        args = list(stage_args)
        if args[:1] in (["run"], ["watch"], ["dev"]):
            args = args[1:]
        argv = ["npx", "vitest", "run", *args]
        if not has_flag(stage_args, "--reporter"):
            out = target_repo.expanduser().resolve() / VITEST_JSON_RELPATH
            argv += ["--reporter=default", "--reporter=json", f"--outputFile.json={out}"]
        if workers > 0 and not has_flag(
            stage_args, "--maxWorkers", "--pool", "--no-file-parallelism"
        ):
            argv.append(f"--maxWorkers={workers}")
        return argv

    def native_report(self, stage_dir: Path) -> NativeReport | None:
        del stage_dir
        return None
