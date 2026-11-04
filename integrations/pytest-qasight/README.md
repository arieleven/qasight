# pytest-qasight

Record every pytest run and see **what broke, what's flaky and what got slower** since the last one.

```bash
pip install pytest-qasight
pytest --qasight          # records this run in the local history (SQLite)
qasight flaky --cycle my-project   # the cycle defaults to the project folder's name
pip install 'qasight[api]' && qasight ui    # the web UI on that history
```

This package installs [qasight](https://pypi.org/project/qasight/) (QASight), which
provides the pytest plugin. Installing `qasight` directly gives you exactly the same thing; this
name exists so the plugin shows up next to the other `pytest-*` plugins.

`pytest --qasight-affected` runs only the tests a change can affect, and `pytest --qasight-shard 1/4`
splits the suite by recorded test time. All options:
[pytest --qasight](https://arieleven.github.io/qasight/docs/cli-commands/command-reference/#pytest-qasight).

No install at all: drop a JUnit XML file on <https://arieleven.github.io/qasight/try>.

Source, issues and docs: <https://github.com/arieleven/qasight>. MIT licensed.
