# __DIST__

A QASight plugin written by `qasight plugins new __NAME__`: a framework adapter (`equipment: __NAME__`) and a reporter (`type: __NAME__-summary`).

```bash
pip install -e .
qasight plugins --check        # __NAME__ and __NAME__-summary are listed, status ok
qasight run --cycle unit       # runs tests/ through the plugin; reports/summary.json written
python -m unittest           # the plugin's own check
```

- `pyproject.toml` registers both through the `qasight.frameworks` and `qasight.reporters` entry point groups.
- `__MODULE__/__init__.py` has the adapter (`build_argv` returns the command to run) and the reporter (`publish` gets the collected results).
- `__MODULE__/runner.py` runs unittest and writes one Allure `*-result.json` per test. Replace it with your runner: that file format is all QASight needs to count, diff and track the tests.

Plugin interface: https://arieleven.github.io/qasight/docs/cli-commands/command-reference/#plugins
