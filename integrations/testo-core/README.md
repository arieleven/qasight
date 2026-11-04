# testo-core is now qasight

Testosterone was renamed to **QASight** in version 1.6, and its package from
`testo-core` to [`qasight`](https://pypi.org/project/qasight/).

```bash
pip install qasight            # instead of testo-core
pip install 'qasight[api]'     # the extras have the same names
```

This package only installs `qasight`, so `pip install -U testo-core` keeps
working, and keeps old imports (`import testo_core`, `import testo_api`)
working with a `DeprecationWarning`. The `testo` command, `testosterone.yaml`,
`pytest --testo` and the `TESTO_*` settings all still work with qasight itself.
