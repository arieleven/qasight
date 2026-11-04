# Getting started example

The project from [Getting Started](../../docs/Getting%20Started.md): two pytest tests and a one-stage `qasight.yaml`.

```bash
cd examples/getting-started
pip install qasight pytest
qasight run --cycle unit
```

CI runs this folder through the GitHub Action on every pull request, so the guide's steps keep working.
