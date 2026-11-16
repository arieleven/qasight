"""End-to-end check of the CLI paths an agent (or a person in a terminal) uses.

QASight is CLI-first: most people run it through a coding agent in the background,
so the commands below are checked as real processes, the way an agent calls them,
on the history `qasight demo` records. CI runs this in the `cli end-to-end` job.

Checks: `qasight doctor` with no config, `qasight ingest` printing the id every other
surface knows, `qasight try` on a folder of older result files, `qasight diff` on ids
from MCP, `qasight mcp` over real stdio, and `qasight export` in every format, picked
from the --out extension.

Usage: python scripts/cli_e2e.py [--skip-images]   (--skip-images: no PDF/PNG, which
need a built UI and Playwright's Chromium)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess  # nosec B404 - runs the qasight CLI with argv lists, no shell
import sys
import tempfile
from pathlib import Path
from typing import Any

QASIGHT = shutil.which("qasight") or "qasight"


def run(
    *args: str, cwd: Path, env: dict[str, str], expect: int | None = 0
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # nosec B603 - argv list
        [QASIGHT, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=600
    )
    if expect is not None and proc.returncode != expect:
        sys.exit(
            f"FAIL qasight {' '.join(args)}: exit {proc.returncode}, wanted {expect}\n"
            f"--- stdout\n{proc.stdout[-3000:]}\n--- stderr\n{proc.stderr[-3000:]}"
        )
    return proc


def check(cond: bool, message: str) -> None:
    if not cond:
        sys.exit(f"FAIL {message}")
    print(f"ok   {message}")


class Mcp:
    """A minimal MCP client over the server's stdin and stdout (newline-delimited JSON-RPC)."""

    def __init__(self, cwd: Path, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(  # nosec B603 - argv list
            [QASIGHT, "mcp"],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.next_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                err = self.proc.stderr.read() if self.proc.stderr else ""
                sys.exit(f"FAIL qasight mcp closed during {method}\n{err[-3000:]}")
            message = json.loads(line)
            if message.get("id") == self.next_id:
                if "error" in message:
                    sys.exit(f"FAIL qasight mcp {method}: {message['error']}")
                return message["result"]

    def tool(self, name: str, **arguments: Any) -> Any:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            sys.exit(f"FAIL MCP tool {name}: {result['content']}")
        if "structuredContent" in result:
            return result["structuredContent"]
        return json.loads(result["content"][0]["text"])

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-images", action="store_true", help="Skip the PDF and PNG exports.")
    opts = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="qasight-cli-e2e-"))
    demo = root / "demo"
    # A clean history: no data folder or database from the caller (TESTO_CHROMIUM stays).
    drop = {"DATABASE_URL", "TESTO_HOME", "QASIGHT_HOME", "TESTO_SERVER_URL", "QASIGHT_SERVER_URL"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["TESTO_NO_UPDATE_CHECK"] = "1"
    run("demo", "--dir", str(demo), "--no-ui", cwd=root, env=env)
    env["TESTO_HOME"] = str(demo / ".testo")
    env["DATABASE_URL"] = f"sqlite:///{demo / 'testo_history.db'}"

    # doctor: no qasight.yaml is fine (zero-config use), and DATABASE_URL is still probed.
    empty = root / "empty"
    empty.mkdir()
    doctor = run("doctor", cwd=empty, env=env)
    check(
        "connection probe succeeded" in doctor.stdout, "doctor probes DATABASE_URL with no config"
    )
    check("INFO" in doctor.stdout, "doctor reports a missing config as INFO, exit 0")

    # try: the demo folder keeps run-1.xml ... run-8.xml; only the newest is the latest run.
    tried = run("try", "--cycle", "demo-shop", cwd=demo, env=env)
    check("Left out 7 older result file(s)" in tried.stderr, "try leaves out older result files")
    check("22 passed · 1 failed · 1 skipped" in tried.stdout, "try previews only the latest run")
    check("test_redirect_after_login" in tried.stdout, "try names the latest run's failure")

    # ingest: the printed id is the one MCP, the UI and `qasight diff` know.
    ingested = run(
        "ingest", "reports/run-8.xml", "--cycle", "e2e-ingest", cwd=demo, env=env, expect=1
    )
    match = re.search(r"Recorded run ([0-9a-f-]{36})", ingested.stdout)
    check(match is not None, "ingest prints the recorded run's id")
    assert match is not None
    ingest_id = match.group(1)

    mcp = Mcp(demo, env)
    try:
        init = mcp.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "qasight-cli-e2e", "version": "1"},
            },
        )
        check("tools" in init.get("capabilities", {}), "MCP initialize over stdio")
        mcp.notify("notifications/initialized")
        names = {t["name"] for t in mcp.request("tools/list")["tools"]}
        wanted = {"list_cycles", "list_runs", "get_run", "failing_tests", "flaky_tests"}
        check(wanted <= names, f"MCP lists its tools ({len(names)})")
        runs = mcp.tool("list_runs", cycle="demo-shop")["runs"]
        check(len(runs) == 8, "MCP list_runs returns the 8 demo runs")
        failing = json.dumps(mcp.tool("failing_tests", cycle="demo-shop"))
        check("test_redirect_after_login" in failing, "MCP failing_tests names the new failure")
        flaky = json.dumps(mcp.tool("flaky_tests", cycle="demo-shop"))
        check("test_apply_coupon" in flaky, "MCP flaky_tests finds the flaky test")
        got = mcp.tool("get_run", run_id=ingest_id)
        check(ingest_id in json.dumps(got), "MCP get_run finds the id ingest printed")
    finally:
        mcp.close()

    # diff: ids from MCP (the run ids the UI shows), and the id ingest printed.
    newest, previous = runs[0]["run_id"], runs[1]["run_id"]
    diffed = run("diff", previous, newest, cwd=demo, env=env)
    check("test_redirect_after_login" in diffed.stdout, "diff compares two run ids from MCP")
    run("diff", ingest_id, ingest_id, "--metrics-only", cwd=demo, env=env)
    check(True, "diff takes the id ingest printed")

    # export: every format, taken from the --out extension.
    magic = {
        "json": b"{",
        "csv": "﻿Cycle".encode(),
        "xlsx": b"PK",
        "pptx": b"PK",
        "html": b"<!",
        "pdf": b"%PDF",
        "png": b"\x89PNG",
    }
    for ext, head in magic.items():
        if opts.skip_images and ext in {"pdf", "png", "html"}:
            continue
        out = root / f"report.{ext}"
        run("export", "--period", "quarter", "--out", str(out), cwd=demo, env=env)
        data = out.read_bytes()
        check(data.lstrip()[: len(head)] == head, f"export --out report.{ext} ({len(data)} bytes)")

    shutil.rmtree(root, ignore_errors=True)
    print("CLI end-to-end: all checks passed")


if __name__ == "__main__":
    main()
