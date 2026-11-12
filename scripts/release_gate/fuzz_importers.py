"""Release gate: fuzz every report importer and fail on an unexpected exception.

Hypothesis generates JUnit, TRX and Robot XML and Jest, Playwright, RSpec, k6,
xcresulttool and go test JSON with odd values and wrong-typed fields, and feeds
each file to :func:`import_junit_reports`. A file it cannot read must come back
as a plain error naming the problem; one reported as "could not be read
(SomeError: ...)" is an exception that escaped the importer, and fails the gate.

    python scripts/release_gate/fuzz_importers.py --examples 200
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from qasight.reporting.junit_import import import_junit_reports

ODD = st.one_of(
    st.text(max_size=12),
    st.sampled_from(
        [
            "",
            "-1",
            "1e309",
            "nan",
            "inf",
            "0",
            "1.5",
            "2026-11-11T10:00:00",
            "2026-13-45T99:99:99",
            "9" * 30,
            "-0",
            " 3 ",
            "1,5",
            "PT1M",
            "00:00:01.5",
            "20261111 10:00:00.000",
        ]
    ),
)


def _attrs(keys: list[str]) -> st.SearchStrategy[str]:
    if not keys:
        return st.just("")
    return st.dictionaries(st.sampled_from(keys), ODD, max_size=len(keys)).map(
        lambda d: "".join(f" {k}={quoteattr(v)}" for k, v in d.items())
    )


def _node(
    tag: str, keys: list[str], children=st.just(""), text=st.just("")
) -> st.SearchStrategy[str]:  # noqa: ANN001
    return st.tuples(_attrs(keys), children, text).map(
        lambda t: f"<{tag}{t[0]}>{escape(t[2])}{t[1]}</{tag}>"
    )


def _xml_strategies() -> dict[str, st.SearchStrategy[str]]:
    case_child = st.one_of(
        _node("failure", ["message", "type"], text=st.text(max_size=20)),
        _node("error", ["message", "type"]),
        _node("skipped", ["message"]),
        _node("system-out", [], text=st.text(max_size=20)),
        _node("flakyFailure", ["message"]),
        _node("rerunFailure", ["message"]),
        _node(
            "properties",
            [],
            st.lists(_node("property", ["name", "value"]), max_size=3).map("".join),
        ),
    )
    case = _node(
        "testcase",
        ["name", "classname", "time", "file", "line", "timestamp"],
        st.lists(case_child, max_size=3).map("".join),
    )
    leaf = _node(
        "testsuite", ["name", "tests", "time", "timestamp"], st.lists(case, max_size=2).map("".join)
    )
    suite = _node(
        "testsuite",
        ["name", "tests", "failures", "errors", "skipped", "time", "timestamp", "hostname", "file"],
        st.lists(st.one_of(case, leaf), max_size=4).map("".join),
    )
    junit = st.one_of(
        suite,
        _node("testsuites", ["name", "tests", "time"], st.lists(suite, max_size=3).map("".join)),
    )

    trx_result = _node(
        "UnitTestResult",
        ["testId", "testName", "outcome", "duration", "startTime", "endTime", "executionId"],
        st.lists(
            _node(
                "Output",
                [],
                _node("ErrorInfo", [], _node("Message", [], text=st.text(max_size=10))),
            ),
            max_size=1,
        ).map("".join),
    )
    trx_def = _node("UnitTest", ["id", "name"], _node("TestMethod", ["className", "name"]))
    trx = st.tuples(st.lists(trx_result, max_size=4), st.lists(trx_def, max_size=3)).map(
        lambda t: (
            '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
            f"<Results>{''.join(t[0])}</Results><TestDefinitions>{''.join(t[1])}</TestDefinitions></TestRun>"
        )
    )

    status = _node(
        "status", ["status", "starttime", "endtime", "start", "elapsed"], text=st.text(max_size=10)
    )
    rtest = _node("test", ["name", "id"], st.lists(status, max_size=2).map("".join))
    inner = _node("suite", ["name"], st.lists(rtest, max_size=2).map("".join))
    rsuite = _node(
        "suite",
        ["name", "id", "source"],
        st.lists(st.one_of(rtest, status, inner), max_size=4).map("".join),
    )
    robot = st.lists(rsuite, max_size=2).map(
        lambda s: f'<robot generator="Robot 7">{"".join(s)}</robot>'
    )
    return {"junit": junit, "trx": trx, "robot": robot}


def _json_strategies() -> dict[str, st.SearchStrategy[str]]:
    scalar = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**70), max_value=2**70),
        st.floats(allow_nan=False),
        st.text(max_size=8),
    )
    val = st.recursive(
        scalar,
        lambda c: st.one_of(
            st.lists(c, max_size=3), st.dictionaries(st.text(max_size=6), c, max_size=3)
        ),
        max_leaves=12,
    )

    def keyed(keys: list[str]) -> st.SearchStrategy[dict]:
        return st.dictionaries(st.sampled_from(keys), val, max_size=len(keys))

    jest = st.fixed_dictionaries(
        {
            "numTotalTests": val,
            "testResults": st.lists(
                st.fixed_dictionaries(
                    {
                        "name": val,
                        "assertionResults": st.lists(
                            keyed(
                                [
                                    "title",
                                    "fullName",
                                    "status",
                                    "duration",
                                    "ancestorTitles",
                                    "failureMessages",
                                    "invocations",
                                    "retryReasons",
                                    "location",
                                ]
                            ),
                            max_size=3,
                        ),
                    },
                    optional={"startTime": val, "endTime": val, "status": val, "message": val},
                ),
                max_size=3,
            ),
        },
        optional={"startTime": val, "success": val},
    )
    results = st.lists(
        keyed(
            ["status", "duration", "startTime", "retry", "error", "errors", "attachments", "stdout"]
        ),
        max_size=3,
    )
    tests = st.lists(
        st.fixed_dictionaries(
            {"results": results}, optional={"projectName": val, "status": val, "annotations": val}
        ),
        max_size=2,
    )
    specs = st.lists(
        st.fixed_dictionaries(
            {"title": val, "tests": tests}, optional={"line": val, "file": val, "ok": val}
        ),
        max_size=2,
    )
    playwright = st.fixed_dictionaries(
        {
            "suites": st.lists(
                st.fixed_dictionaries(
                    {"title": val, "file": val, "specs": specs}, optional={"suites": val}
                ),
                max_size=2,
            )
        },
        optional={"stats": val, "config": val},
    )
    rspec = st.fixed_dictionaries(
        {
            "version": val,
            "examples": st.lists(
                keyed(
                    [
                        "id",
                        "description",
                        "full_description",
                        "status",
                        "file_path",
                        "line_number",
                        "run_time",
                        "exception",
                        "pending_message",
                    ]
                ),
                max_size=3,
            ),
        },
        optional={"summary": val},
    )
    k6 = st.fixed_dictionaries(
        {
            "root_group": st.fixed_dictionaries(
                {"checks": val}, optional={"groups": val, "name": val}
            ),
            "metrics": val,
        },
        optional={"state": val},
    )
    xcresult = st.fixed_dictionaries({"devices": val, "testNodes": val}, optional={"title": val})
    gotest = st.lists(
        keyed(["Time", "Action", "Package", "Test", "Elapsed", "Output"]), min_size=1, max_size=6
    ).map(lambda events: "\n".join(json.dumps(e) for e in events))
    return {
        "jest": jest.map(json.dumps),
        "playwright": playwright.map(json.dumps),
        "rspec": rspec.map(json.dumps),
        "k6": k6.map(json.dumps),
        "xcresult": xcresult.map(json.dumps),
        "gotest": gotest,
    }


def _fuzz(
    kind: str,
    strategy: st.SearchStrategy[str],
    ext: str,
    examples: int,
    escaped: Counter[tuple[str, str]],
    samples: dict[tuple[str, str], str],
) -> None:
    """Import ``examples`` generated files of one format, recording escaped exceptions."""

    @settings(
        max_examples=examples,
        deadline=None,
        database=None,
        suppress_health_check=list(HealthCheck),
        phases=[Phase.generate],
    )
    @given(strategy)
    def check(body: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / f"report{ext}").write_text(body, encoding="utf-8")
            result = import_junit_reports(
                target_repo=root, patterns=(f"*{ext}",), results_dir=root / "out"
            )
            for error in result.errors:
                if "could not be read (" in error:
                    key = (kind, error.split("could not be read (", 1)[1][:80])
                    escaped[key] += 1
                    samples.setdefault(key, body[:300])

    check()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--examples", type=int, default=200, help="generated files per format")
    args = parser.parse_args()

    escaped: Counter[tuple[str, str]] = Counter()
    samples: dict[tuple[str, str], str] = {}
    formats = {k: (s, ".xml" if k != "trx" else ".trx") for k, s in _xml_strategies().items()}
    formats |= {k: (s, ".json") for k, s in _json_strategies().items()}
    for kind, (strategy, ext) in formats.items():
        _fuzz(kind, strategy, ext, args.examples, escaped, samples)
        print(f"{kind}: {args.examples} files", flush=True)

    for (kind, error), count in escaped.most_common():
        print(f"::error::{kind}: {count} file(s) raised {error}\n  e.g. {samples[(kind, error)]}")
    print(f"unexpected exceptions: {sum(escaped.values())}")
    return 1 if escaped else 0


if __name__ == "__main__":
    sys.exit(main())
