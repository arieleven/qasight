import { NavLink } from "react-router-dom";

const TABS = [
  { to: "/settings", label: "Project", hint: "Where the history is kept, how long runs stay, and the settings in effect" },
  { to: "/settings/ai", label: "AI", hint: "The model that writes failure summaries and answers questions" },
  { to: "/resources", label: "Resources", hint: "Pools of machines, devices or accounts that parallel workers lease" }
];

/**
 * The settings pages, one menu entry: the project (history, retention, settings in
 * effect), AI, and resource pools. Shown under the header of each.
 */
export function SettingsTabs() {
  return (
    <nav aria-label="Settings" className="-mt-2 mb-6 flex gap-1 overflow-x-auto border-b border-white/10 text-sm">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end
          title={tab.hint}
          className={({ isActive }) =>
            [
              "-mb-px whitespace-nowrap border-b-2 px-3 py-2 font-semibold transition-colors",
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
