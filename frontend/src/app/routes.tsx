import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "./AppShell";

import { NotFoundPage } from "./NotFoundPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";

// The dashboard is the landing page and loads with the app; every other page
// is its own chunk, fetched the first time someone opens it.
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
        lazy: () => import("../features/cycles/CyclesPage").then((m) => ({ Component: m.CyclesPage }))
      },
      {
        path: "cycles/:name",
        lazy: () => import("../features/cycles/CycleDetailPage").then((m) => ({ Component: m.CycleDetailPage }))
      },
      {
        path: "cycles/:name/test",
        lazy: () => import("../features/tests/TestPage").then((m) => ({ Component: m.TestPage }))
      },
      {
        path: "runs",
        lazy: () => import("../features/history/HistoryPage").then((m) => ({ Component: m.HistoryPage }))
      },
      {
        path: "runs/:runId",
        lazy: () => import("../features/run-detail/RunDetailPage").then((m) => ({ Component: m.RunDetailPage }))
      },
      {
        path: "compare",
        lazy: () => import("../features/compare/ComparePage").then((m) => ({ Component: m.ComparePage }))
      },
      {
        path: "releases",
        lazy: () => import("../features/releases/ReleasesPage").then((m) => ({ Component: m.ReleasesPage }))
      },
      {
        path: "releases/:release",
        lazy: () => import("../features/releases/ReleasesPage").then((m) => ({ Component: m.ReleasePage }))
      },
      {
        path: "health",
        lazy: () => import("../features/health/ProjectHealthPage").then((m) => ({ Component: m.ProjectHealthPage }))
      },
      {
        path: "scorecard",
        lazy: () => import("../features/scorecard/ScorecardPage").then((m) => ({ Component: m.ScorecardPage }))
      },
      {
        path: "report",
        lazy: () => import("../features/report/ReportPage").then((m) => ({ Component: m.ReportPage }))
      },
      {
        path: "ask",
        lazy: () => import("../features/ask/AskPage").then((m) => ({ Component: m.AskPage }))
      },
      {
        path: "resources",
        lazy: () => import("../features/resources/ResourcesPage").then((m) => ({ Component: m.ResourcesPage }))
      },
      {
        path: "quick-run",
        lazy: () => import("../features/execution/QuickRunPage").then((m) => ({ Component: m.QuickRunPage }))
      },
      {
        path: "try",
        lazy: () => import("../features/try/TryPage").then((m) => ({ Component: m.TryPage }))
      },
      {
        path: "settings/ai",
        lazy: () => import("../features/settings/AIIntegrationSettingsPage").then((m) => ({ Component: m.AIIntegrationSettingsPage }))
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
