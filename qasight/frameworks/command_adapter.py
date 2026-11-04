"""Generic ``command`` adapter — run any test runner as a stage.

The stage's ``args`` are the complete argv (``["npx", "jest", "--ci"]``,
``["npx", "playwright", "test"]``, ``["maestro", "test", "flows/"]``). Nothing
is injected: such runners don't speak Allure, so their results arrive through
the stage's ``junit_xml`` globs, which the executor converts into Allure
result files after the process exits (:mod:`qasight.reporting.junit_import`).
The Allure results dir is still exported as ``TESTO_SHARED_ALLURE_RESULTS_DIR``
for runners that can write Allure JSON themselves.
"""

from __future__ import annotations

from pathlib import Path

from qasight.frameworks.base import NativeReport


class CommandAdapter:
    name: str = "command"

    def results_subdir(self) -> str:
        return "command"

    def build_argv(
        self,
        *,
        target_repo: Path,
        results_dir: Path,
        stage_args: tuple[str, ...],
        workers: int,
    ) -> list[str]:
        del target_repo, results_dir, workers  # the command owns its own flags
        if not stage_args:
            # The loader rejects this; guard direct callers too.
            raise ValueError("framework 'command' needs args (the full command to run)")
        return list(stage_args)

    def native_report(self, stage_dir: Path) -> NativeReport | None:
        del stage_dir
        return None
