# qasight (npm)

[QASight](https://github.com/arieleven/qasight) for JavaScript and TypeScript projects, without installing Python:

```bash
npx qasight init          # find your test runners, write qasight.yaml and a CI workflow, record a first run
npx qasight run -- npm test
npx qasight ui            # the run history in your browser
```

Every `qasight` command and flag works the same way; see the [Command Reference](https://arieleven.github.io/qasight/docs/cli-commands/command-reference/).

## How it works

QASight is a Python package. This one runs it with [uv](https://docs.astral.sh/uv/), a single binary that downloads a Python and the package into its own cache: the first run takes a few seconds, the next ones start at once. uv is taken from `$QASIGHT_UV`, then your `PATH`, then this package's cache folder (`~/.cache/qasight-npm`, `%LOCALAPPDATA%\qasight-npm` on Windows, or `$QASIGHT_NPM_HOME`). When there is none it is installed there with uv's official installer, which changes nothing in your shell profile.

| Variable | Meaning |
|----------|---------|
| `QASIGHT_VERSION` | The qasight version to run (`1.6.0`), or `latest` to check PyPI. Default: the newest one uv has cached |
| `QASIGHT_UV` | The uv binary to use |
| `QASIGHT_NPM_HOME` | Where uv is installed when there is none |

Install messages go to stderr, so `qasight run --ci` output stays machine-readable.

With Python already installed you can skip this package: `pipx run qasight` or `uvx qasight`.
