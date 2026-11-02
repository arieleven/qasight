import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "react-router-dom";

import { router } from "./app/routes";
import { StaticBuildBanner } from "./components/StaticBuildBanner";
import { StandaloneReport, embeddedReport } from "./features/report/standalone";
import { shouldRetry } from "./lib/api-client";
import { installStaticBackend } from "./lib/static-backend";
import "./styles.css";

// No-op unless the build was made with VITE_STATIC_DATA_BASE (the GitLab Pages
// demo), where it answers API requests from exported JSON. Must run before the
// first render so the initial queries hit the shim.
installStaticBackend();

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: shouldRetry } } });

// `testo export` bakes a report into the page: render just that document.
const embedded = embeddedReport();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {embedded ? (
      <StandaloneReport embedded={embedded} />
    ) : (
      <QueryClientProvider client={queryClient}>
        <StaticBuildBanner />
        <RouterProvider router={router} />
      </QueryClientProvider>
    )}
  </React.StrictMode>
);
