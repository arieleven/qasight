import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient, type IssueStatusItem } from "../../lib/api-client";
import { Badge, Card } from "../../components/ui";
import { countLabel, testPath } from "../../lib/format";

// Issues shown before "Show all".
const SHOWN = 10;

const STATE_TONE: Record<string, "danger" | "brand" | "success"> = {
  failing: "danger",
  flaky: "brand",
  passing: "success"
};

const STATE_TEXT: Record<string, string> = {
  failing: "Red tests",
  flaky: "Flaky tests",
  passing: "Passing"
};

function IssueName({ item }: { item: IssueStatusItem }) {
  if (!item.url) return <span className="break-all font-semibold text-ink-100">{item.issue}</span>;
  return (
    <a href={item.url} target="_blank" rel="noreferrer noopener" className="break-all font-semibold text-brand-300 hover:underline">
      {item.issue}
    </a>
  );
}

/**
 * Which issues have red tests: each issue the cycle's tests point at (issue
 * links and `issue` labels), failing first, with the tests behind it. Hidden
 * when no test points at an issue.
 */
export function CycleIssuesCard({ cycle }: { cycle: string }) {
  const query = useQuery({
    queryKey: ["cycle-issues", cycle],
    queryFn: () => apiClient.getCycleIssues(cycle)
  });
  const items = query.data?.items ?? [];
  if (items.length === 0) return null;
  const data = query.data!;
  const red = items.filter((i) => i.state === "failing").length;

  return (
    <Card title="Issues">
      <p className="mb-3 text-sm text-ink-200">
        {red > 0 ? (
          <span className="text-danger-300">{countLabel(red, "issue has", "issues have")} red tests</span>
        ) : (
          <span>No issue has a red test</span>
        )}{" "}
        <span className="text-ink-400">out of {countLabel(items.length, "issue")} the tests point at.</span>
      </p>
      <ul className="divide-fade text-sm">
        {items.slice(0, SHOWN).map((item) => (
          <li key={item.issue} className="py-2">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <IssueName item={item} />
              <span className="flex items-baseline gap-2 text-xs text-ink-300">
                {countLabel(item.tests, "test")}
                {item.failing > 0 ? <span className="text-danger-300">{item.failing} failing</span> : null}
                <Badge tone={STATE_TONE[item.state] ?? "success"}>{STATE_TEXT[item.state] ?? item.state}</Badge>
              </span>
            </div>
            {item.state !== "passing" ? (
              <ul className="mt-1 space-y-0.5 text-xs">
                {item.examples
                  .filter((t) => t.last_status === "failed" || t.last_status === "broken" || t.flaky)
                  .slice(0, 5)
                  .map((t) => (
                    <li key={t.key}>
                      <Link to={testPath(cycle, t.key)} className="break-all text-brand-300 hover:underline">
                        {t.name}
                      </Link>{" "}
                      <span className="text-ink-400">{t.flaky && t.last_status === "passed" ? "flaky" : t.last_status}</span>
                    </li>
                  ))}
              </ul>
            ) : null}
          </li>
        ))}
        {items.length > SHOWN ? (
          <li className="pt-2 text-xs text-ink-400">and {countLabel(items.length - SHOWN, "more issue")}</li>
        ) : null}
      </ul>
      <p className="mt-3 text-xs text-ink-400">
        {data.linked_tests} of {data.total_tests} tests point at an issue, across the last {data.runs_considered} runs. Red
        means a test failed in its latest run.
      </p>
    </Card>
  );
}
