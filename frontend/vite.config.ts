import { defineConfig } from "vitest/config";

export default defineConfig({
  // "/" for local dev and the API-served build; the GitLab Pages job sets
  // VITE_BASE to the project subpath the site is published under (e.g. "/qasight/").
  base: process.env.VITE_BASE || "/",
  build: {
    rolldownOptions: {
      // React Router and React Query mark files "use client" for server components;
      // this is a client-only SPA, so the directive has no meaning here.
      onwarn(warning, warn) {
        if (warning.code === "MODULE_LEVEL_DIRECTIVE") return;
        warn(warning);
      }
    }
  },
  server: {
    port: Number(process.env.PORT) || 5173,
    strictPort: false
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"]
  }
});
