import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "./AppShell";

import { NotFoundPage } from "./NotFoundPage";
import { AskPage } from "../features/ask/AskPage";
import { ComparePage } from "../features/compare/ComparePage";
import { CycleDetailPage } from "../features/cycles/CycleDetailPage";
import { CyclesPage } from "../features/cycles/CyclesPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { QuickRunPage } from "../features/execution/QuickRunPage";
import { ProjectHealthPage } from "../features/health/ProjectHealthPage";
import { HistoryPage } from "../features/history/HistoryPage";
import { ReleasePage, ReleasesPage } from "../features/releases/ReleasesPage";
import { ReportPage } from "../features/report/ReportPage";
import { ResourcesPage } from "../features/resources/ResourcesPage";
import { RunDetailPage } from "../features/run-detail/RunDetailPage";
import { TestPage } from "../features/tests/TestPage";
import { TryPage } from "../features/try/TryPage";
import { AIIntegrationSettingsPage } from "../features/settings/AIIntegrationSettingsPage";

export const routes = [
  {
    path: "/",
    element: <AppShell />,
    children: [
      {
        index: true,
        element: <DashboardPage />
      },
      {
        path: "cycles",
        element: <CyclesPage />
      },
      {
        path: "cycles/:name",
        element: <CycleDetailPage />
      },
      {
        path: "cycles/:name/test",
        element: <TestPage />
      },
      {
        path: "runs",
        element: <HistoryPage />
      },
      {
        path: "runs/:runId",
        element: <RunDetailPage />
      },
      {
        path: "compare",
        element: <ComparePage />
      },
      {
        path: "releases",
        element: <ReleasesPage />
      },
      {
        path: "releases/:release",
        element: <ReleasePage />
      },
      {
        path: "health",
        element: <ProjectHealthPage />
      },
      {
        path: "report",
        element: <ReportPage />
      },
      {
        path: "ask",
        element: <AskPage />
      },
      {
        path: "resources",
        element: <ResourcesPage />
      },
      {
        path: "quick-run",
        element: <QuickRunPage />
      },
      {
        path: "try",
        element: <TryPage />
      },
      {
        path: "settings/ai",
        element: <AIIntegrationSettingsPage />
      },
      {
        path: "*",
        element: <NotFoundPage />
      }
    ]
  }
];

/** The path the app is mounted under: Vite's base, or the `<base href>` when the build is relative. */
export function appBasename(): string {
  const base = import.meta.env.BASE_URL;
  if (base.startsWith("/")) return base;
  return new URL(document.baseURI).pathname;
}

export const router = createBrowserRouter(routes, {
  // The static demo is served from a project subpath on Pages, so the router
  // resolves routes against Vite's base, or the page's <base href> for a build
  // made with a relative base (the bundled UI, see vite.config.ts).
  basename: appBasename()
});
