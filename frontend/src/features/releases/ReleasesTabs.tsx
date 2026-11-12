import { NavLink } from "react-router-dom";

const TABS = [
  { to: "/releases", label: "Test results", hint: "How each release's runs did" },
  { to: "/code-quality", label: "Code quality", hint: "Linters, security, API, web and load tools, graded A-E per version" }
];

/**
 * The two views of a release, one menu entry: what its tests did (Releases) and how its
 * code scores (Code quality, the scorecard). Shown under the header of both pages.
 */
export function ReleasesTabs() {
  return (
    <nav aria-label="Release views" className="-mt-2 mb-6 flex gap-1 border-b border-white/10 text-sm">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end
          title={tab.hint}
          className={({ isActive }) =>
            [
              "-mb-px border-b-2 px-3 py-2 font-semibold transition-colors",
              isActive ? "border-brand-400 text-white" : "border-transparent text-ink-300 hover:text-white"
            ].join(" ")
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  );
}
