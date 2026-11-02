import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient, type LabelCountItem } from "../../lib/api-client";
import { Card } from "../../components/ui";
import { cyclePath } from "../../lib/format";

// Values shown per label name before "and N more".
const VALUES_PER_NAME = 8;

const NAME_TITLES: Record<string, string> = {
  epic: "Epics",
  feature: "Features",
  story: "Stories",
  severity: "Severity",
  owner: "Owners",
  tag: "Tags",
  issue: "Issues"
};

function nameTitle(name: string): string {
  return NAME_TITLES[name] ?? name.charAt(0).toUpperCase() + name.slice(1);
}

function groupByName(items: LabelCountItem[]): [string, LabelCountItem[]][] {
  const groups = new Map<string, LabelCountItem[]>();
  for (const item of items) {
    groups.set(item.name, [...(groups.get(item.name) ?? []), item]);
  }
  return [...groups.entries()];
}

/**
 * The cycle's tests counted by label: "Checkout: 12 tests, 3 failing".
 * Labels come from the results (Allure labels, pytest markers, Playwright and
 * Behave tags, JUnit test case properties). Hidden when no test has one.
 */
export function CycleLabelsCard({ cycle }: { cycle: string }) {
  const query = useQuery({
    queryKey: ["cycle-labels", cycle],
    queryFn: () => apiClient.getCycleLabels(cycle)
  });
  const items = query.data?.items ?? [];
  if (items.length === 0) return null;
  const data = query.data!;

  return (
    <Card title="By label">
      <div className="grid gap-5 md:grid-cols-2">
        {groupByName(items).map(([name, values]) => (
          <div key={name}>
            <h3 className="mb-2 text-sm font-semibold text-white">{nameTitle(name)}</h3>
            <ul className="space-y-1.5 text-sm">
              {values.slice(0, VALUES_PER_NAME).map((item) => (
                <li key={item.filter} className="flex items-baseline justify-between gap-3">
                  <Link
                    to={`${cyclePath(cycle)}?label=${encodeURIComponent(item.filter)}#tests`}
                    className="min-w-0 break-all text-brand-300 hover:underline"
                  >
                    {item.value}
                  </Link>
                  <span className="shrink-0 text-xs text-ink-300">
                    {item.tests} {item.tests === 1 ? "test" : "tests"}
                    {item.failing > 0 ? <span className="text-danger-300">, {item.failing} failing</span> : null}
                    {item.flaky > 0 ? <span className="text-brand-300">, {item.flaky} flaky</span> : null}
                  </span>
                </li>
              ))}
              {values.length > VALUES_PER_NAME ? (
                <li className="text-xs text-ink-400">and {values.length - VALUES_PER_NAME} more</li>
              ) : null}
            </ul>
          </div>
        ))}
      </div>
      <p className="mt-4 text-xs text-ink-400">
        {data.labelled_tests} of {data.total_tests} tests carry a label, across the last {data.runs_considered} runs. Failing
        counts each test's latest outcome.
      </p>
    </Card>
  );
}
