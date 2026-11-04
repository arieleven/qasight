import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

// "/" for local dev and the team server image; the Pages demo sets VITE_BASE to
// the project subpath it is published under (e.g. "/qasight/"). The UI
// bundled into the wheel (scripts/build_ui_bundle.sh) is built with "./", so
// its asset URLs are relative and one build works at any path: `qasight ui`
// serves it at "/", and `qasight export-site` rewrites the <base href> below to
// the subpath a static site is published under.
const base = process.env.VITE_BASE || "/";

/** A relative build gets `<base href="/">`, so deep links like /runs/<id> still find ./assets. */
function baseHref(): Plugin {
  return {
    name: "qasight-base-href",
    transformIndexHtml(html) {
      if (base.startsWith("/")) return html;
      return html.replace("<head>", '<head>\n    <base href="/" />');
    }
  };
}

export default defineConfig({
  base,
  plugins: [baseHref()],
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
