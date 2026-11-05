"""Plugins: framework adapters and reporters from ``qasight.frameworks`` /
``qasight.reporters`` entry points, exercised through the example plugin in
``examples/plugin`` as if it were pip-installed."""

from __future__ import annotations

import json
import os
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight import plugins
from qasight.cli.app import app
from qasight.config.errors import ConfigValidationError
from qasight.config.loader import discover_and_load
from qasight.config.resolver import resolve_plan
from qasight.frameworks.base import get_adapter
from qasight.reporting.reporters.factory import ReporterFactory
from qasight.services.cycle_run import CycleRunOptions, CycleRunService
from tests.fixtures.engine import NoopRenderer

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "plugin"


def _install(
    site: Path, dist: str, entry_points: str, modules: dict[str, str] | None = None
) -> None:
    """Write what ``pip install`` leaves behind: a dist-info with entry points."""
    info = site / f"{dist.replace('-', '_')}-0.1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist}\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(textwrap.dedent(entry_points), encoding="utf-8")
    for name, source in (modules or {}).items():
        (site / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh site-packages on sys.path, with the example plugin installed."""
    root = tmp_path / "site"
    root.mkdir()
    _install(
        root,
        "qasight-example-plugin",
        """\
        [qasight.frameworks]
        unittest = qasight_example_plugin:UnittestAdapter
        [qasight.reporters]
        json-summary = qasight_example_plugin:JsonSummaryReporter
        """,
    )
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(EXAMPLE))
    # The stage subprocess imports the plugin's runner module too.
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(filter(None, [str(EXAMPLE), os.environ.get("PYTHONPATH")]))
    )
    plugins.clear_cache()
    yield root
    plugins.clear_cache()


def test_installed_plugins_are_discovered(site: Path) -> None:
    found = {(p.kind, p.name): p for p in plugins.list_plugins()}

    assert set(found) == {("framework", "unittest"), ("reporter", "json-summary")}
    assert found["framework", "unittest"].package == "qasight-example-plugin 0.1.0"
    assert found["framework", "unittest"].target == "qasight_example_plugin:UnittestAdapter"
    assert {"pytest", "unittest"} <= plugins.framework_names()
    assert {"allure", "json-summary"} <= plugins.reporter_types()


def test_get_adapter_and_reporter_factory_load_plugins(site: Path) -> None:
    from qasight.config.schema import ReporterSpec

    assert get_adapter("unittest").name == "unittest"
    (reporter,) = ReporterFactory.build(
        config_reporters=(ReporterSpec(type="json-summary", options=()),)
    )
    assert reporter.reporter_type == "json-summary"
    assert [
        r.reporter_type
        for r in ReporterFactory.build(config_reporters=(), overrides=["json-summary"])
    ] == ["json-summary"]


def test_cycle_runs_a_plugin_stage_and_reporter(site: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (project / "tests" / "test_math.py").write_text(
        (EXAMPLE / "tests" / "test_math.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (project / "qasight.yaml").write_text(
        (EXAMPLE / "qasight.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    cfg = discover_and_load(config_path=project / "qasight.yaml")

    outcome = CycleRunService().run(
        cfg=cfg,
        plan=resolve_plan(cfg, plan_name="unit"),
        renderer=NoopRenderer(),
        options=CycleRunOptions(persist=False, report_db=False),
    )

    assert outcome.exit_code == 0
    summary = json.loads((project / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert (summary["cycle"], summary["total"], summary["passed"]) == ("unit", 2, 2)


def test_unknown_framework_error_lists_plugins(site: Path, tmp_path: Path) -> None:
    config = tmp_path / "qasight.yaml"
    config.write_text(
        "version: 1\ncycles:\n  c:\n    stages:\n      - {name: s, equipment: nope}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigValidationError, match="unittest"):
        discover_and_load(config_path=config)


def test_builtin_names_and_duplicates_are_ignored(site: Path) -> None:
    _install(
        site,
        "testo-shadow",
        """\
        [qasight.frameworks]
        pytest = shadow:Adapter
        unittest = shadow:Adapter
        """,
    )
    plugins.clear_cache()

    names = {p.name: p.package for p in plugins.list_plugins() if p.kind == "framework"}
    # Discovery order between the two dists is up to importlib.metadata.
    assert set(names) == {"unittest"}
    assert type(get_adapter("pytest")).__module__ == "qasight.frameworks.pytest_adapter"


def test_broken_plugins_raise_plugin_error_and_fail_check(site: Path) -> None:
    _install(
        site,
        "testo-broken",
        """\
        [qasight.frameworks]
        crashes = broken_mod:Missing
        halfdone = broken_mod:HalfAdapter
        [qasight.reporters]
        notareporter = broken_mod:HalfAdapter
        """,
        modules={
            "broken_mod": """\
            class HalfAdapter:
                name = "halfdone"
                def build_argv(self, **kwargs):
                    return []
            """
        },
    )
    plugins.clear_cache()

    with pytest.raises(plugins.PluginError, match="testo-broken 0.1.0.*failed to import"):
        get_adapter("crashes")
    with pytest.raises(plugins.PluginError, match="missing: results_subdir, native_report"):
        get_adapter("halfdone")
    with pytest.raises(plugins.PluginError, match="not a BaseReporter subclass"):
        plugins.load_reporter("notareporter")

    result = CliRunner().invoke(app, ["plugins", "--check", "--json"])
    assert result.exit_code == 1
    rows = {r["name"]: r["status"] for r in json.loads(result.stdout)}
    assert rows["unittest"] == "ok"
    assert rows["json-summary"] == "ok"
    assert rows["crashes"].startswith("error:")


def test_plugins_command_without_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugins, "_entry_points", lambda kind: {})

    result = CliRunner().invoke(app, ["plugins"])

    assert result.exit_code == 0
    assert "pytest" in result.stdout
    assert "No plugins installed" in result.stdout


_CTOR_BOOM = {
    "ctor_boom": """\
    from qasight.reporting.reporters.base import BaseReporter

    class Boom:
        def __init__(self):
            raise RuntimeError("adapter ctor boom")

    class BoomReporter(BaseReporter):
        reporter_type = "boomrep"
        def __init__(self, *, options=None):
            raise RuntimeError("reporter ctor boom")
        def publish(self, **kwargs):
            raise AssertionError("never built")
    """
}


def test_a_plugin_constructor_that_raises_is_a_plugin_error(site: Path) -> None:
    _install(
        site,
        "testo-ctor-boom",
        """\
        [qasight.frameworks]
        boomfw = ctor_boom:Boom
        [qasight.reporters]
        boomrep = ctor_boom:BoomReporter
        """,
        modules=_CTOR_BOOM,
    )
    plugins.clear_cache()
    with pytest.raises(plugins.PluginError, match="failed to start: adapter ctor boom"):
        get_adapter("boomfw")
    with pytest.raises(plugins.PluginError, match="failed to start: reporter ctor boom"):
        ReporterFactory.build(config_reporters=(), overrides=["boomrep"])


def test_one_broken_reporter_plugin_does_not_turn_off_the_others(site: Path) -> None:
    _install(
        site,
        "testo-ctor-boom",
        """\
        [qasight.reporters]
        boomrep = ctor_boom:BoomReporter
        missingrep = ctor_boom:NotThere
        """,
        modules=_CTOR_BOOM,
    )
    plugins.clear_cache()
    failures: list = []
    built = ReporterFactory.build(
        config_reporters=(),
        overrides=["boomrep", "json-summary", "missingrep"],
        failures=failures,
    )
    assert [r.reporter_type for r in built] == ["json-summary"]
    assert len(failures) == 2 and not any(f.ok for f in failures)
    assert any("reporter ctor boom" in f.message for f in failures)
    assert any("failed to import" in f.message for f in failures)
