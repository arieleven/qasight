# qasight-upload

One-file uploader that sends a CI job's test results to a QASight team server, with no Python. Go standard library only.

```bash
go test -race ./...          # unit tests
go build -o qasight-upload . # build
../scripts/upload_binary_smoke.sh   # upload to a real qasight-api from this checkout
```

How to use it, flags and exit codes: [Upload Without Python](../docs/Processes%20&%20Guides/Upload%20Without%20Python.md). Why it exists: [Go Uploader - 2026-11-20](../docs/Specs%20&%20ADRs/Go%20Uploader%20-%202026-11-20.md).
