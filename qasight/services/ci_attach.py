"""Add QASight to the GitHub Actions workflows a project already has (``qasight init --attach``).

Plain ``qasight init`` writes ``.github/workflows/qasight.yml``, which runs the
tests again next to the workflow that already runs them: twice the CI minutes
and two files to keep in step. :func:`plan_attach` reads the existing
workflows instead and, for each job whose steps run a test command it knows,

* makes that command also write a result file QASight reads, when the runner
  needs a flag for that (``pytest --junitxml``, ``jest --json --outputFile``,
  ``dotnet test --logger trx`` ...; Maven and Gradle write theirs already),
* adds one QASight step after the job's last test step, with
  ``if: ${{ !cancelled() }}`` so failing runs are recorded too and ``results``
  set so the Action records those files instead of running a cycle,
* adds ``pull-requests: write`` to a ``permissions:`` block that lacks it
  (the pull request comment needs it).

The files are edited as text, line by line, so comments, quoting and key
order stay as they were; PyYAML's node positions say which lines to touch. A
command it can't extend safely (it isn't the last thing on its line, or it is
inside a folded scalar) is left alone and listed, so nothing is guessed.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from qasight.services.ci_templates import ACTION_REF

# A step that already uses QASight (or its old names) means the job is done.
_OURS = re.compile(r"(arieleven/qasight|taltal-beep/testosterone|integrations/github-action)")
# After the command, any of these means more shell follows, so appending isn't safe.
_UNSAFE_TAIL = re.compile(r"&&|\|\||[;|<>`]|\\\s*$|\$\(")


@dataclass(frozen=True)
class _Rule:
    key: str
    label: str
    pattern: re.Pattern[str]
    flag: str = ""  # appended to the command; "" = the runner writes its results already
    result: str = ""  # where the results land, relative to the step's working directory
    present: re.Pattern[str] | None = None  # the user already asked for a result file


def _rx(text: str) -> re.Pattern[str]:
    return re.compile(text)


# The command starts a shell command: at the line start or after a separator, past any
# `VAR=value`, `env`, `time` or `xvfb-run` in front of it (so `pip install pytest` is no match).
_PRE = r"(?:^|[;&|(])\s*(?:(?:env|time|xvfb-run(?:\s+-a)?)\s+)?(?:\w+=\S*\s+)*"
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "pytest",
        "pytest",
        _rx(
            _PRE + r"(?:(?:uv run|poetry run|pipenv run|hatch run|pdm run)\s+)?"
            r"(?:python3?\s+-m\s+)?pytest\b"
        ),
        " --junitxml=qasight-pytest.xml",
        "qasight-pytest.xml",
        _rx(r"--junit-?xml"),
    ),
    _Rule(
        "jest",
        "Jest",
        _rx(_PRE + r"(?:npx\s+|yarn\s+|pnpm\s+(?:exec\s+)?)?jest\b"),
        " --json --outputFile=qasight-jest.json",
        "qasight-jest.json",
        _rx(r"--outputFile|--json"),
    ),
    _Rule(
        "vitest",
        "Vitest",
        _rx(_PRE + r"(?:npx\s+|yarn\s+|pnpm\s+(?:exec\s+)?)?vitest\b"),
        " --reporter=default --reporter=json --outputFile.json=qasight-vitest.json",
        "qasight-vitest.json",
        _rx(r"--outputFile|--reporter"),
    ),
    _Rule(
        "dotnet",
        "dotnet test",
        _rx(_PRE + r"dotnet\s+test\b"),
        " --logger trx --results-directory qasight-dotnet",
        "qasight-dotnet/*.trx",
        _rx(r"--logger"),
    ),
    _Rule(
        "rspec",
        "RSpec",
        _rx(_PRE + r"(?:bundle\s+exec\s+)?rspec\b"),
        " --format progress --format json --out qasight-rspec.json",
        "qasight-rspec.json",
        _rx(r"--out\b"),
    ),
    _Rule(
        "phpunit",
        "PHPUnit",
        _rx(_PRE + r"(?:vendor/bin/|\./vendor/bin/)?phpunit\b"),
        " --log-junit qasight-phpunit.xml",
        "qasight-phpunit.xml",
        _rx(r"--log-junit"),
    ),
    _Rule(
        "maven",
        "Maven",
        _rx(_PRE + r"(?:\./mvnw|mvn)\b.*\b(?:test|verify|install|package)\b"),
        result="**/target/*-reports/TEST-*.xml",
    ),
    _Rule(
        "gradle",
        "Gradle",
        _rx(_PRE + r"(?:\./gradlew|gradle)\b.*\b(?:test|check|build)\b"),
        result="**/build/test-results/**/TEST-*.xml",
    ),
)
# Runners whose result file needs more than a flag (a reporter change, a redirect): listed, not edited.
_ADVICE: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "Playwright",
        _rx(_PRE + r"(?:npx\s+)?playwright\s+test\b"),
        "add a json or junit reporter to playwright.config "
        "(reporter: [['list'], ['json', {outputFile: 'qasight-playwright.json'}]])",
    ),
    (
        "go test",
        _rx(_PRE + r"go\s+test\b"),
        "add `-json` and send its output to a file: go test -json ./... > qasight-go.json",
    ),
)
_NPM_SCRIPT = re.compile(
    _PRE
    + r"(?P<tool>npm|pnpm|yarn)\s+(?:run\s+)?(?P<script>test(?::[\w:-]+)?|[\w:-]*test[\w:-]*)\b"
)


@dataclass(frozen=True)
class AttachedStep:
    """One test command found in a workflow, and what was done with it."""

    workflow: str  # path relative to the repository
    job: str
    runner: str  # label, e.g. "pytest"
    command: str  # the line as it was
    result: str  # where its results land, relative to the repository ("" = none)
    edited: bool  # the command line was changed


@dataclass
class AttachPlan:
    texts: dict[Path, str] = field(default_factory=dict)  # workflow -> new text
    originals: dict[Path, str] = field(default_factory=dict)
    steps: list[AttachedStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def diff(self, repo: Path) -> str:
        """A unified diff of every edited workflow."""
        out: list[str] = []
        for path, text in self.texts.items():
            rel = path.relative_to(repo).as_posix()
            out += difflib.unified_diff(
                self.originals[path].splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        return "".join(out)


def _get(node: Node | None, key: str) -> Node | None:
    if not isinstance(node, MappingNode):
        return None
    for k, v in node.value:
        if isinstance(k, ScalarNode) and k.value == key:
            return v
    return None


def _scalar(node: Node | None) -> str:
    return node.value if isinstance(node, ScalarNode) else ""


def _command_lines(run: ScalarNode, lines: list[str]) -> list[tuple[int, str]]:
    """``(file line, shell text)`` for each line of a ``run:`` value that can be edited in place.

    The shell text of a one-line value is the line from the value on (no ``run:`` key), so
    a command at its start is found; edits still go on the end of the file line.
    """
    if run.style == "|":
        first = run.start_mark.line + 1
        count = len(run.value.rstrip("\n").split("\n"))
        return [(first + i, lines[first + i]) for i in range(count) if first + i < len(lines)]
    if run.style in (None, '"', "'") and run.start_mark.line == run.end_mark.line:
        line = lines[run.start_mark.line]
        start = run.start_mark.column + (1 if run.style else 0)
        return [(run.start_mark.line, line[start:])]
    return []  # folded or multi-line flow scalars: not edited


def _package_scripts(repo: Path, workdir: str) -> dict[str, str]:
    for base in (repo / workdir, repo):
        try:
            data = json.loads((base / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if isinstance(scripts, dict):
            return {str(k): str(v) for k, v in scripts.items()}
    return {}


def _match(line: str, repo: Path, workdir: str) -> tuple[_Rule, int, str] | None:
    """The rule a shell line runs, where the command's own arguments start, and the text to append."""
    code = re.split(r"\s+#", line, maxsplit=1)[0]
    script = _NPM_SCRIPT.search(code)
    if script:
        body = _package_scripts(repo, workdir).get(script.group("script"), "")
        for rule in _RULES:
            if rule.key in ("jest", "vitest") and rule.pattern.search(body):
                # npm needs `--` to pass flags to the script; pnpm and yarn pass them as they are
                # (pnpm would hand a literal `--` to Jest, which reads what follows as patterns).
                sep = " --" if script.group("tool") == "npm" else ""
                return rule, script.end(), sep + rule.flag
        return None
    for rule in _RULES:
        found = rule.pattern.search(code)
        if found:
            return rule, found.end(), rule.flag
    return None


def _insert_before_comment(line: str, text: str, *, quoted: str | None) -> str:
    line = line.rstrip("\n")
    comment = re.search(r"\s+#", line)
    body, rest = (line[: comment.start()], line[comment.start() :]) if comment else (line, "")
    stripped = body.rstrip()
    if quoted and stripped.endswith(quoted):
        stripped = stripped[:-1] + text + quoted
    else:
        stripped += text
    return stripped + rest


def _last_content_line(node: Node, lines: list[str]) -> int:
    end = node.end_mark.line
    if end < len(lines) and lines[end][: node.end_mark.column].strip():
        end += 1  # the node ends partway through this line, so the line is its own
    end = min(end, len(lines))
    while end > node.start_mark.line + 1 and (
        not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")
    ):
        end -= 1
    return end  # insert before this index


def _join(workdir: str, rel: str) -> str:
    if not workdir or workdir in (".", "./"):
        return rel
    return (PurePosixPath(workdir) / rel).as_posix()


def _step_block(
    lines: list[str], step: MappingNode, *, results: list[str], cycle: str
) -> list[str]:
    key_col = step.start_mark.column
    dash_col = lines[step.start_mark.line].rfind("-", 0, key_col)
    dash = " " * max(dash_col, 0)
    key = " " * key_col
    return [
        f"{dash}- name: QASight",
        f"{key}if: ${{{{ !cancelled() }}}}  # record failing runs too",
        f"{key}uses: {ACTION_REF}",
        f"{key}with:",
        f"{key}  results: {json.dumps(' '.join(results))}  # record these instead of running tests again",
        f"{key}  cycle: {json.dumps(cycle)}",
        f'{key}  comment-on-pr: "true"',
    ]


def _permissions_edit(perms: Node | None, lines: list[str]) -> tuple[int, str] | str | None:
    """Where to add ``pull-requests: write`` (index, line), a note, or ``None`` when nothing to do."""
    if perms is None:
        return None
    if isinstance(perms, MappingNode) and not perms.flow_style:
        if _get(perms, "pull-requests") is not None:
            if _scalar(_get(perms, "pull-requests")) != "write":
                return "its permissions don't give `pull-requests: write`, which the comment needs"
            return None
        if not perms.value:
            return None
        col = perms.value[0][0].start_mark.column
        return _last_content_line(
            perms, lines
        ), " " * col + "pull-requests: write  # QASight's comment"
    if _scalar(perms) == "write-all":
        return None
    return "its permissions don't give `pull-requests: write`, which the comment needs"


def plan_attach(repo: Path, *, cycle: str) -> AttachPlan:
    """What :func:`apply` would change in *repo*'s GitHub workflows; nothing is written."""
    repo = repo.expanduser().resolve()
    plan = AttachPlan()
    folder = repo / ".github" / "workflows"
    files = sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")]) if folder.is_dir() else []
    attached_jobs: list[tuple[Path, str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            root = yaml.compose(text)
        except yaml.YAMLError:
            plan.notes.append(f"{path.name}: not valid YAML, left alone")
            continue
        jobs = _get(root, "jobs")
        if not isinstance(jobs, MappingNode):
            continue
        lines = text.splitlines()
        inserts: list[tuple[int, list[str]]] = []
        line_edits: dict[int, str] = {}
        rel = path.relative_to(repo).as_posix()
        top_dir = _scalar(_get(_get(_get(root, "defaults"), "run"), "working-directory"))
        perms_done = False
        for job_key, job in jobs.value:
            steps = _get(job, "steps")
            if not isinstance(steps, SequenceNode) or steps.flow_style:
                continue
            if any(_OURS.search(_scalar(_get(s, "uses"))) for s in steps.value):
                plan.notes.append(f"{rel} job {job_key.value!r} already uses QASight")
                continue
            job_dir = _scalar(_get(_get(_get(job, "defaults"), "run"), "working-directory"))
            results: list[str] = []
            last: MappingNode | None = None
            for step in steps.value:
                run = _get(step, "run")
                if not isinstance(step, MappingNode) or not isinstance(run, ScalarNode):
                    continue
                workdir = _scalar(_get(step, "working-directory")) or job_dir or top_dir
                quoted = run.style if run.style in ('"', "'") else None
                for index, line in _command_lines(run, lines):
                    found = _match(line, repo, workdir)
                    if found is None:
                        for label, pattern, advice in _ADVICE:
                            if pattern.search(re.split(r"\s+#", line, maxsplit=1)[0]):
                                plan.notes.append(f"{rel} job {job_key.value!r}: {label}: {advice}")
                        continue
                    rule, start, flag = found
                    code = re.split(r"\s+#", line, maxsplit=1)[0]
                    tail = code[start:]
                    if quoted:
                        tail = tail.rstrip().rstrip(quoted)
                    edited = False
                    if rule.flag and rule.present is not None and rule.present.search(tail):
                        plan.notes.append(
                            f"{rel} job {job_key.value!r}: {rule.label} already writes a result "
                            "file; add its path to the step's `results`"
                        )
                        continue
                    if flag:
                        if _UNSAFE_TAIL.search(tail):
                            plan.notes.append(
                                f"{rel} job {job_key.value!r}: `{line.strip()}` isn't the last "
                                f"command on its line, so add `{flag.strip()}` to it yourself"
                            )
                            continue
                        line_edits[index] = _insert_before_comment(
                            lines[index], flag, quoted=quoted
                        )
                        edited = True
                    result = _join(workdir, rule.result)
                    if result not in results:
                        results.append(result)
                    last = step
                    plan.steps.append(
                        AttachedStep(
                            rel, str(job_key.value), rule.label, line.strip(), result, edited
                        )
                    )
            if last is None:
                continue
            attached_jobs.append((path, str(job_key.value)))
            inserts.append(
                (
                    _last_content_line(last, lines),
                    _step_block(lines, last, results=results, cycle=f"{{cycle}}#{job_key.value}"),
                )
            )
            perms = _get(job, "permissions") or _get(root, "permissions")
            if not perms_done or _get(job, "permissions") is not None:
                edit = _permissions_edit(perms, lines)
                if isinstance(edit, tuple):
                    inserts.append((edit[0], [edit[1]]))
                    perms_done = _get(job, "permissions") is None
                elif isinstance(edit, str):
                    plan.notes.append(f"{rel} job {job_key.value!r}: {edit}")
        if not inserts:
            continue
        new = list(lines)
        for index, replacement in line_edits.items():
            new[index] = replacement
        for index, block in sorted(inserts, key=lambda item: item[0], reverse=True):
            new[index:index] = block
        plan.originals[path] = text
        plan.texts[path] = "\n".join(new) + ("\n" if text.endswith("\n") else "")

    # One cycle per attached job; with one job it gets the plain name.
    several = len(attached_jobs) > 1
    for path, body in plan.texts.items():
        for _, job in [j for j in attached_jobs if j[0] == path]:
            name = f"{cycle}-{job}" if several else cycle
            body = body.replace(json.dumps(f"{{cycle}}#{job}"), json.dumps(name))
        plan.texts[path] = body
    return plan


def apply(plan: AttachPlan) -> list[Path]:
    """Write the edited workflows; returns them."""
    for path, text in plan.texts.items():
        path.write_text(text, encoding="utf-8")
    return list(plan.texts)


def has_test_steps(repo: Path) -> bool:
    """Whether a workflow already runs tests that ``--attach`` could record."""
    return bool(plan_attach(repo, cycle="tests").steps)
