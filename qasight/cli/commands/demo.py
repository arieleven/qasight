"""``qasight demo`` — a throwaway history with something to find, then the web UI on it.

For someone who has just installed QASight and has no results of their
own at hand: it writes eight JUnit XML files for a small made-up shop app
into a new folder (a temporary one unless ``--dir`` is given), records them
with ``qasight ingest`` as eight runs of the ``demo-shop`` cycle, and opens
``qasight ui`` on that folder's history. The story the runs tell:

* ``test_apply_coupon`` is **flaky** (fails in runs 2, 5 and 7, passes otherwise),
* ``test_redirect_after_login`` is a **new failure** in the latest run,
* ``test_results_are_ranked`` **got slower** in the last two runs (0.2 s to about 1.9 s),
* ``test_badge_count`` was **fixed** after run 3.

Everything stays inside the demo folder: its own SQLite file and report
copies (``DATABASE_URL`` and ``TESTO_HOME`` point there), so the user's real
history is never touched. Nothing is executed apart from ``qasight`` itself.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import typer

from qasight.engine.exit_codes import EngineExitCode

DEMO_CYCLE = "demo-shop"
DEMO_RUNS = 8
_FLAKY_RUNS = frozenset({2, 5, 7})
_FIXED_AFTER = 3
_SLOW_FROM = 7

# (suite, test, base seconds)
_TESTS: tuple[tuple[str, str, float], ...] = (
    ("tests.test_cart", "test_add_item", 0.04),
    ("tests.test_cart", "test_remove_item", 0.03),
    ("tests.test_cart", "test_badge_count", 0.05),
    ("tests.test_cart", "test_empty_cart_message", 0.02),
    ("tests.test_cart", "test_quantity_limits", 0.06),
    ("tests.test_checkout", "test_total_includes_tax", 0.08),
    ("tests.test_checkout", "test_apply_coupon", 0.12),
    ("tests.test_checkout", "test_expired_coupon_rejected", 0.07),
    ("tests.test_checkout", "test_shipping_options", 0.09),
    ("tests.test_checkout", "test_payment_declined", 0.15),
    ("tests.test_checkout", "test_order_confirmation_email", 0.22),
    ("tests.test_login", "test_login_ok", 0.11),
    ("tests.test_login", "test_wrong_password", 0.09),
    ("tests.test_login", "test_redirect_after_login", 0.13),
    ("tests.test_login", "test_logout_clears_session", 0.06),
    ("tests.test_login", "test_password_reset_link", 0.18),
    ("tests.test_search", "test_empty_query", 0.03),
    ("tests.test_search", "test_results_are_ranked", 0.21),
    ("tests.test_search", "test_filters_by_price", 0.14),
    ("tests.test_search", "test_typo_suggestions", 0.17),
    ("tests.test_search", "test_pagination", 0.10),
    ("tests.test_profile", "test_update_address", 0.08),
    ("tests.test_profile", "test_avatar_upload", 0.26),
    ("tests.test_profile", "test_dark_mode_setting", 0.02),
)
_SKIPPED = frozenset({"test_dark_mode_setting"})


@dataclass(frozen=True)
class _Case:
    suite: str
    name: str
    seconds: float
    failure: str | None = None
    skipped: bool = False


def demo_cases(run: int) -> list[_Case]:
    """The test outcomes of demo run *run* (1-based). Deterministic."""
    rng = random.Random(run * 7919)
    cases: list[_Case] = []
    for suite, name, base in _TESTS:
        seconds = round(base * rng.uniform(0.9, 1.1), 3)
        failure: str | None = None
        if name == "test_apply_coupon" and run in _FLAKY_RUNS:
            failure = "TimeoutError: coupon service did not answer within 2 s"
        elif name == "test_redirect_after_login" and run == DEMO_RUNS:
            failure = "AssertionError: expected redirect to '/account', got '/login?next=/account'"
        elif name == "test_badge_count" and run <= _FIXED_AFTER:
            failure = "AssertionError: badge shows 0 items, cart has 2"
        if name == "test_results_are_ranked" and run >= _SLOW_FROM:
            seconds = round(1.9 * rng.uniform(0.95, 1.05), 3)
        cases.append(_Case(suite, name, seconds, failure, skipped=name in _SKIPPED))
    return cases


def demo_junit_xml(run: int) -> str:
    """JUnit XML for demo run *run*, the way pytest ``--junitxml`` writes it."""
    cases = demo_cases(run)
    failures = sum(1 for c in cases if c.failure)
    skipped = sum(1 for c in cases if c.skipped)
    total = round(sum(c.seconds for c in cases), 3)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<testsuites>",
        f'  <testsuite name="pytest" tests="{len(cases)}" failures="{failures}" '
        f'errors="0" skipped="{skipped}" time="{total}">',
    ]
    for c in cases:
        head = (
            f"    <testcase classname={quoteattr(c.suite)} name={quoteattr(c.name)} "
            f'time="{c.seconds}"'
        )
        if c.failure:
            lines.append(head + ">")
            lines.append(
                f"      <failure message={quoteattr(c.failure)}>{escape(c.failure)}</failure>"
            )
            lines.append("    </testcase>")
        elif c.skipped:
            lines.append(head + ">")
            lines.append('      <skipped message="needs a real browser" />')
            lines.append("    </testcase>")
        else:
            lines.append(head + " />")
    lines += ["  </testsuite>", "</testsuites>", ""]
    return "\n".join(lines)


def _demo_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["TESTO_HOME"] = str(root / ".testo")
    env["DATABASE_URL"] = f"sqlite:///{(root / 'testo_history.db').as_posix()}"
    # A demo run is not a CI run, whatever the shell it was started from says.
    for key in ("CI", "GITHUB_ACTIONS", "GITLAB_CI"):
        env.pop(key, None)
    return env


def _testo(args: list[str], *, root: Path, env: dict[str, str], quiet: bool) -> int:
    cmd = [sys.executable, "-c", "from qasight.cli.app import main; main()", *args]
    out = subprocess.DEVNULL if quiet else None
    return subprocess.call(cmd, cwd=root, env=env, stdout=out, stderr=out)  # noqa: S603


def demo(
    directory: Path = typer.Option(
        None,
        "--dir",
        file_okay=False,
        help="Folder for the demo history (default: a new temporary folder). Must be empty or new.",
    ),
    ui: bool = typer.Option(
        True, "--ui/--no-ui", help="Open the web UI on the demo history when it's recorded."
    ),
    port: int = typer.Option(8000, "--port", "-p", min=1, max=65535, help="Port for the UI."),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the UI in the default browser."
    ),
) -> None:
    """Record eight sample runs (a flaky test, a new failure, a slower test) and open the UI on them."""
    if directory is None:
        root = Path(tempfile.mkdtemp(prefix="testo-demo-"))
    else:
        root = directory.expanduser().resolve()
        if root.exists() and any(root.iterdir()):
            typer.echo(f"{root} is not empty; pick a new folder for the demo.", err=True)
            raise typer.Exit(int(EngineExitCode.INVALID_INPUT))
        root.mkdir(parents=True, exist_ok=True)

    reports = root / "reports"
    reports.mkdir()
    env = _demo_env(root)
    typer.echo(f"Recording {DEMO_RUNS} runs of the '{DEMO_CYCLE}' cycle in {root}")
    for run in range(1, DEMO_RUNS + 1):
        report = reports / f"run-{run}.xml"
        report.write_text(demo_junit_xml(run), encoding="utf-8")
        code = _testo(
            ["ingest", f"reports/{report.name}", "--cycle", DEMO_CYCLE, "--stage", "pytest"],
            root=root,
            env=env,
            quiet=True,
        )
        # 1 just means the run had failing tests, which is the point.
        if code not in (int(EngineExitCode.SUCCESS), int(EngineExitCode.DOMAIN_FAILURE)):
            typer.echo(f"Recording run {run} failed (exit {code}); rerun to see why:", err=True)
            typer.echo(f"  cd {root} && qasight ingest reports/{report.name} --cycle {DEMO_CYCLE}")
            raise typer.Exit(code)
        typer.echo(f"  run {run}/{DEMO_RUNS} recorded")

    typer.echo(
        "\nWhat to look for:\n"
        "  - test_apply_coupon is flaky (failed in runs 2, 5 and 7)\n"
        "  - test_redirect_after_login is a new failure in the latest run\n"
        "  - test_results_are_ranked got slower (0.2 s -> about 1.9 s)\n"
        "  - test_badge_count was fixed after run 3\n"
        f"\nSame history from the CLI (in {root}):\n"
        f"  TESTO_HOME={env['TESTO_HOME']} DATABASE_URL={env['DATABASE_URL']} "
        f"qasight flaky --cycle {DEMO_CYCLE}\n"
    )
    if not ui:
        return
    args = ["ui", "--port", str(port), "--open" if open_browser else "--no-open"]
    try:
        code = _testo(args, root=root, env=env, quiet=False)
    except KeyboardInterrupt:
        code = 0
    raise typer.Exit(code)
