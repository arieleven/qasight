"""Native Behave adapter — pairs the CLI with the Allure formatter."""

from __future__ import annotations

from pathlib import Path

from qasight.frameworks.base import NativeReport


class BehaveAdapter:
    name: str = "behave"

    def results_subdir(self) -> str:
        return "behave"

    def build_argv(
        self,
        *,
        target_repo: Path,
        results_dir: Path,
        stage_args: tuple[str, ...],
        workers: int,
    ) -> list[str]:
        del workers  # native behave is single-process
        results_dir.mkdir(parents=True, exist_ok=True)
        argv: list[str] = [
            "behave",
            "-f",
            "allure_behave.formatter:AllureFormatter",
            "-o",
            str(results_dir.resolve()),
        ]
        argv.extend(stage_args)

        # If the user didn't pass an explicit features path / -k / etc., default to <repo>/features.
        has_target = any(
            (arg and not str(arg).startswith("-"))
            or str(arg).endswith(".feature")
            or "--paths" in str(arg)
            for arg in stage_args
        )
        if not has_target:
            features = (target_repo.expanduser().resolve() / "features").resolve()
            argv.append(str(features))
        return argv

    def native_report(self, stage_dir: Path) -> NativeReport | None:
        del stage_dir  # native Behave has no HTML report of its own (Allure only)
        return None
