/**
 * JUnit XML read in the browser, for the try-it page: nothing is uploaded.
 *
 * Covers the shapes the CLI's `qasight ingest` reads (qasight/reporting/junit_import.py):
 * a <testsuites> root or a bare <testsuite>, nested suites, and per-case
 * <failure>, <error> and <skipped>. Times are seconds, as JUnit writes them; a
 * thousands comma ("1,234.5", as some Maven locales write it) is dropped.
 */

export type JunitStatus = "passed" | "failed" | "broken" | "skipped";

export interface JunitCase {
  name: string;
  classname: string;
  suite: string;
  file: string;
  status: JunitStatus;
  durationMs: number | null;
  message: string;
}

export interface JunitGroup {
  message: string;
  names: string[];
}

export interface JunitReport {
  cases: JunitCase[];
  totals: Record<JunitStatus, number> & { total: number; durationMs: number };
  /** Failures that share a message once numbers, ids and quoted values are blanked. */
  groups: JunitGroup[];
}

export class JunitParseError extends Error {}

const SIGNATURE = /0x[0-9a-fA-F]+|\d+(?:\.\d+)?|'[^']*'|"[^"]*"/g;

/** Same rule as `run_failures.message_signature` on the server. */
export function messageSignature(message: string): string {
  return message.replace(SIGNATURE, "…").trim().toLowerCase();
}

function firstLine(text: string | null | undefined): string {
  const line = (text ?? "").trim().split("\n")[0] ?? "";
  return line.length > 300 ? `${line.slice(0, 299)}…` : line;
}

function seconds(value: string | null): number | null {
  if (value == null || value.trim() === "") return null;
  const parsed = Number(value.replace(/,/g, ""));
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed * 1000) : null;
}

function caseOf(element: Element): JunitCase {
  const failure = element.querySelector(":scope > failure");
  const error = element.querySelector(":scope > error");
  const skipped = element.querySelector(":scope > skipped");
  const problem = failure ?? error;
  const status: JunitStatus = failure ? "failed" : error ? "broken" : skipped ? "skipped" : "passed";
  const suite = element.closest("testsuite")?.getAttribute("name") ?? "";
  return {
    name: element.getAttribute("name") ?? "(unnamed)",
    classname: element.getAttribute("classname") ?? "",
    suite,
    file: element.getAttribute("file") ?? "",
    status,
    durationMs: seconds(element.getAttribute("time")),
    message: problem ? firstLine(problem.getAttribute("message") || problem.textContent) : ""
  };
}

/** Parse one JUnit XML document; throws JunitParseError when it isn't one. */
export function parseJunit(xml: string): JunitReport {
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  if (doc.getElementsByTagName("parsererror").length > 0) {
    throw new JunitParseError("This file isn't valid XML.");
  }
  const root = doc.documentElement;
  if (root.tagName !== "testsuites" && root.tagName !== "testsuite") {
    throw new JunitParseError(`Expected a JUnit <testsuites> or <testsuite> root, found <${root.tagName}>.`);
  }
  const cases = Array.from(doc.getElementsByTagName("testcase")).map(caseOf);
  return summarise(cases);
}

/** Merge several files into one report, as `qasight ingest 'reports/*.xml'` would. */
export function summarise(cases: JunitCase[]): JunitReport {
  const totals = { passed: 0, failed: 0, broken: 0, skipped: 0, total: cases.length, durationMs: 0 };
  const bySignature = new Map<string, JunitGroup>();
  for (const c of cases) {
    totals[c.status] += 1;
    totals.durationMs += c.durationMs ?? 0;
    if ((c.status === "failed" || c.status === "broken") && c.message) {
      const key = messageSignature(c.message);
      const group = bySignature.get(key) ?? { message: c.message, names: [] };
      group.names.push(c.name);
      bySignature.set(key, group);
    }
  }
  const groups = [...bySignature.values()].filter((g) => g.names.length > 1).sort((a, b) => b.names.length - a.names.length);
  return { cases, totals, groups };
}

/** A small report to show before anyone has a file at hand. */
export const SAMPLE_JUNIT = `<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="api" tests="4">
    <testcase classname="tests.api.test_orders" name="test_create_order" time="0.120"/>
    <testcase classname="tests.api.test_orders" name="test_cancel_order" time="0.910">
      <failure message="ConnectionRefusedError: [Errno 111] Connection refused to 10.0.0.12:5432"/>
    </testcase>
    <testcase classname="tests.api.test_users" name="test_get_user" time="0.300">
      <failure message="ConnectionRefusedError: [Errno 111] Connection refused to 10.0.0.13:5432"/>
    </testcase>
    <testcase classname="tests.api.test_users" name="test_list_users" time="0.080"/>
  </testsuite>
  <testsuite name="ui" tests="2">
    <testcase classname="tests.ui.test_login" name="test_login_form" time="2.400">
      <error message="TimeoutError: page.click: Timeout 30000ms exceeded"/>
    </testcase>
    <testcase classname="tests.ui.test_login" name="test_remember_me" time="0">
      <skipped message="flaky on CI"/>
    </testcase>
  </testsuite>
</testsuites>
`;
