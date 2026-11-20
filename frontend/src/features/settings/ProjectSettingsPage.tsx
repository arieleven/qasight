import { useQuery } from "@tanstack/react-query";

import { Card, PageHeader } from "../../components/ui";
import { apiClient } from "../../lib/api-client";
import { IS_STATIC_BUILD } from "../../lib/static-backend";
import { RunSettingsNote } from "../history/RunSettingsNote";
import { StorageNote } from "../history/StorageNote";
import { SettingsTabs } from "./SettingsTabs";

/**
 * Settings → Project: where the history lives, how long runs are kept, how full the disk
 * is, and every project setting with where its value came from (the same rows as
 * `qasight doctor`). Read-only: the values live in qasight.yaml's `settings:` and the
 * environment, which win in that order reversed (an exported variable wins).
 */
export function ProjectSettingsPage() {
  const query = useQuery({
    queryKey: ["run-settings"],
    queryFn: () => apiClient.getRunSettings(),
    enabled: !IS_STATIC_BUILD
  });
  const settings = query.data?.settings ?? [];
  const set = settings.filter((s) => s.source !== "default");
  return (
    <section>
      <PageHeader
        title="Settings"
        subtitle="How QASight is set up for this project. Change values in qasight.yaml (settings:) or the environment."
      />
      <SettingsTabs />
      {IS_STATIC_BUILD ? (
        <p className="text-sm text-ink-300">The demo has no project settings to show.</p>
      ) : (
        <div className="space-y-6">
          <Card title="Project" copy={false}>
            <p className="text-sm text-ink-300" data-testid="project-folder">
              <span className="font-medium text-ink-200">Project folder:</span>{" "}
              <code>{query.data?.project_folder ?? "…"}</code>
            </p>
            <RunSettingsNote />
            <StorageNote />
          </Card>
          <Card title="Settings in effect" copy={false}>
            {set.length === 0 ? (
              <p className="text-sm text-ink-300">
                Everything is at its default. Set values under <code>settings:</code> in qasight.yaml, for example{" "}
                <code>settings: {"{"}quarantine: {"{"}auto: 0.3{"}"}{"}"}</code>.
              </p>
            ) : null}
            <ul className="divide-y divide-white/5 text-sm" data-testid="project-settings">
              {settings.map((s) => (
                <li key={s.env} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                  <span>
                    <code className="text-ink-100">{s.name}</code>{" "}
                    <span className="text-xs text-ink-400">({s.env})</span>
                  </span>
                  <span className={s.source === "default" ? "text-ink-400" : "text-ink-100"}>
                    {s.value ?? "default"}
                    {s.source !== "default" ? <span className="ml-2 text-xs text-ink-400">from {s.source}</span> : null}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </section>
  );
}
