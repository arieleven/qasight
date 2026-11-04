"""Cypress adapter — ``npx cypress run`` with results QASight can read.

The stage's ``args`` are passed to ``cypress run`` (``--spec``, ``--browser``,
``--config``...). Unless ``args`` set ``--reporter``, the adapter adds Cypress's
built-in ``junit`` reporter, one file per spec under
``<target_repo>/test-results/testo-cypress-<hash>.xml``, imported after the stage
(``default_junit_xml``). Screenshots and videos stay in Cypress's own folders.

``workers`` has no effect: Cypress runs specs one after another unless its paid
cloud splits them. Cypress itself must be installed in the target project.
"""

from __future__ import annotations

from pathlib import Path

from qasight.frameworks.base import NativeReport
from qasight.frameworks.jest_adapter import has_flag

JUNIT_GLOB = "test-results/testo-cypress-*.xml"


class CypressAdapter:
    name: str = "cypress"
    default_junit_xml: tuple[str, ...] = (JUNIT_GLOB,)

    def results_subdir(self) -> str:
        return "cypress"

    def build_argv(
        self,
        *,
        target_repo: Path,
        results_dir: Path,
        stage_args: tuple[str, ...],
        workers: int,
    ) -> list[str]:
        del results_dir, workers
        args = list(stage_args)
        if args[:1] == ["run"]:
            args = args[1:]
        argv = ["npx", "cypress", "run", *args]
        if not has_flag(stage_args, "--reporter", "-r"):
            folder = target_repo.expanduser().resolve() / "test-results"
            argv += [
                "--reporter",
                "junit",
                "--reporter-options",
                f"mochaFile={folder}/testo-cypress-[hash].xml,toConsole=false",
            ]
        return argv

    def native_report(self, stage_dir: Path) -> NativeReport | None:
        del stage_dir
        return None
