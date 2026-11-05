# Example plugin

A complete QASight plugin in two short files: a framework adapter that runs the standard library's `unittest` and a reporter that writes the run's counts to JSON. Copy it as the starting point for your own runner or reporter.

```bash
pip install qasight -e examples/plugin
cd examples/plugin
qasight plugins --check        # unittest and json-summary are listed, status ok
qasight run --cycle unit       # 2 passed; reports/summary.json written
```

- [`pyproject.toml`](pyproject.toml) registers both through the `qasight.frameworks` and `qasight.reporters` entry point groups.
- [`qasight_example_plugin/__init__.py`](qasight_example_plugin/__init__.py) has the adapter (`build_argv` returns the command) and the reporter (`publish` receives the collected results).
- [`qasight_example_plugin/runner.py`](qasight_example_plugin/runner.py) writes one Allure `*-result.json` per test into the results directory the adapter is given, which is all QASight needs to count, diff and track the tests.

The plugin interface is described in the [Command Reference](../../docs/CLI%20Commands/Command%20Reference.md#plugins).
