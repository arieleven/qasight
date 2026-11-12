import { useCallback, useEffect, useRef, useState, type ComponentType, type RefObject } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { useIsFetching, useIsMutating, useQuery } from "@tanstack/react-query";

import { apiClient } from "../lib/api-client";
import { EYE_BRAND_GREEN, EyeLogo } from "../components/mascot";
import { scoreTone } from "../features/health/score";
import { DemoBadge } from "../components/StaticBuildBanner";
import { Icons } from "../components/ui";

/** A menu entry. `also` lists pages reached from it (a button on its page) that keep it highlighted. */
type NavLinkSpec = { to: string; label: string; icon: ComponentType; end?: boolean; also?: string[] };
type NavGroup = { title?: string; links: NavLinkSpec[] };

/**
 * The menu, grouped by the question each page answers: what needs me now,
 * how are my tests doing, how is each release doing, what do I hand to others,
 * and how QASight is set up. Compare, Quick run and Try a file are buttons on
 * the pages they belong to (Runs, Cycles, the welcome page), not menu items.
 */
export const NAV_GROUPS: NavGroup[] = [
  { links: [{ to: "/", label: "Overview", icon: Icons.HomeIcon, end: true }] },
  {
    title: "Tests",
    links: [
      { to: "/runs", label: "Runs", icon: Icons.RunsIcon, also: ["/compare"] },
      { to: "/cycles", label: "Cycles", icon: Icons.CyclesIcon, also: ["/quick-run"] },
      { to: "/test-health", label: "Test health", icon: Icons.HeartIcon }
    ]
  },
  {
    title: "Releases",
    links: [{ to: "/releases", label: "Releases", icon: Icons.TagIcon, also: ["/code-quality"] }]
  },
  {
    title: "Share",
    links: [
      { to: "/report", label: "Report", icon: Icons.ReportIcon },
      { to: "/ask", label: "Ask", icon: Icons.ChatIcon }
    ]
  },
  {
    title: "Settings",
    links: [
      { to: "/resources", label: "Resources", icon: Icons.KeyIcon },
      { to: "/settings/ai", label: "AI settings", icon: Icons.SparkIcon }
    ]
  }
];

const NAV_LINKS = NAV_GROUPS.flatMap((g) => g.links);

/** Pages without a menu entry of their own: the crumb they show under the entry they belong to. */
const SUB_PAGES: Record<string, string> = {
  "/compare": "Compare",
  "/quick-run": "Quick run",
  "/code-quality": "Code quality",
  "/try": "Try a file"
};

function under(pathname: string, to: string): boolean {
  return to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`);
}

const DOCS_URL = "https://arieleven.github.io/qasight/docs/";

/** Breadcrumb for the current page: the section, then the cycle, run or test it shows. */
function breadcrumbs(pathname: string): { label: string; to?: string }[] {
  const sub = Object.keys(SUB_PAGES).find((to) => under(pathname, to));
  if (sub) {
    const parent = NAV_LINKS.find((l) => l.also?.includes(sub));
    return parent ? [{ label: parent.label, to: parent.to }, { label: SUB_PAGES[sub] }] : [{ label: SUB_PAGES[sub] }];
  }
  const link = NAV_LINKS.find((l) => under(pathname, l.to));
  if (!link) return [{ label: "Not found" }];
  const crumbs: { label: string; to?: string }[] = [{ label: link.label, to: link.to }];
  const rest = pathname.slice(link.to.length).split("/").filter(Boolean).map(decodeURIComponent);
  if (link.to === "/cycles" && rest[0]) {
    crumbs.push({ label: rest[0], to: rest[1] ? `/cycles/${encodeURIComponent(rest[0])}` : undefined });
    if (rest[1] === "test") crumbs.push({ label: "Test" });
  } else if (link.to === "/runs" && rest[0]) {
    crumbs.push({ label: rest[0].slice(0, 8) });
  }
  return crumbs;
}

// Iris colours for the Test health page, the same green/amber/red scale as the score ring.
const IRIS_BY_TONE = { success: "#34d399", warn: "#fbbf24", danger: "#f87171", neutral: "#7e88ae" } as const;

/**
 * The eye logo. On the Test health page its iris takes the colour of the
 * health score; everywhere else it stays brand green. The pupil spins while
 * any request is in flight.
 */
function BrandEye() {
  const { pathname } = useLocation();
  const [searchParams] = useSearchParams();
  const onHealthPage = under(pathname, "/test-health");
  const cycle = searchParams.get("cycle") ?? "";
  // Same key as ProjectHealthPage, so this reads its cached result instead of fetching twice.
  const health = useQuery({
    queryKey: ["project-health", cycle],
    queryFn: () => apiClient.getProjectHealth(cycle || undefined),
    enabled: onHealthPage
  });
  const loading = useIsFetching() + useIsMutating() > 0;
  const iris = onHealthPage && health.data ? IRIS_BY_TONE[scoreTone(health.data.score)] : EYE_BRAND_GREEN;
  return <EyeLogo size={30} irisColor={iris} spinning={loading} />;
}

/**
 * Desktop: a floating glass sidebar (grouped nav, docs link) beside
 * the page. Below `lg` the sidebar is hidden and a top bar with a disclosure
 * menu takes its place.
 */
/** True once the page has scrolled a little, so the top bar can turn to glass over the content. */
function useScrolled(threshold = 8) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > threshold);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [threshold]);
  return scrolled;
}

export function AppShell() {
  const { pathname } = useLocation();
  const scrolled = useScrolled();
  return (
    <div className="min-h-full">
      {/* Inside a landmark, like all content, so screen-reader region navigation finds it. */}
      <nav aria-label="Skip links" className="print:hidden">
        <a
          href="#main"
          // With a <base href> in the page, "#main" would resolve against the base and leave this route.
          onClick={(event) => {
            event.preventDefault();
            document.getElementById("main")?.focus();
          }}
          className="sr-only z-50 rounded-xl bg-brand-400 px-4 py-2 text-sm font-semibold text-ink-950 focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
        >
          Skip to content
        </a>
      </nav>
      <aside className="glass-panel print:hidden fixed inset-y-4 left-4 z-20 hidden w-64 flex-col overflow-y-auto px-4 py-6 lg:flex">
        <Link to="/" className="flex items-center justify-center gap-2.5 pb-6" aria-label="QASight home">
          <BrandEye />
          <span className="text-base font-bold tracking-wide text-white">QASight</span>
        </Link>
        <div className="mb-5 h-px bg-gradient-to-r from-transparent via-white/25 to-transparent" aria-hidden />
        <nav aria-label="Sections" className="flex flex-col gap-1 text-sm">
          <NavGroups />
        </nav>
        <HelpCard />
        <VersionNote />
      </aside>

      <header
        className={`sticky top-0 z-30 print:hidden border-b backdrop-blur-xl transition-colors duration-300 lg:hidden ${
          scrolled ? "border-white/10 bg-ink-950/70 shadow-lg shadow-black/30" : "border-white/5 bg-ink-950/40"
        }`}
      >
        <div className="relative flex items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link to="/" className="flex items-center gap-2" aria-label="QASight home">
            <BrandEye />
            <span className="text-base font-bold tracking-wide text-white">QASight</span>
          </Link>
          <div className="flex items-center gap-3">
            <DemoBadge />
            <UserBadge />
            <HealthDot />
            <MobileMenu />
          </div>
        </div>
      </header>

      <div className="lg:pl-[18rem] print:pl-0">
        <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:pt-5 print:max-w-none print:p-0">
          <header
            data-scrolled={scrolled || undefined}
            className={`sticky top-3 z-20 -mx-4 mb-5 hidden print:hidden items-center justify-between gap-4 rounded-2xl border px-4 py-2.5 transition-[background-color,border-color,box-shadow] duration-300 lg:flex ${
              scrolled
                ? "border-white/10 bg-gradient-to-br from-white/[0.05] to-white/[0.01] bg-ink-900 shadow-xl shadow-black/30 backdrop-blur-2xl"
                : "border-transparent"
            }`}
          >
            <nav aria-label="Breadcrumb" className="text-xs text-ink-300">
              <ol className="flex flex-wrap items-center gap-1">
                <li className="flex items-center gap-1.5">
                  <Link to="/" className="text-ink-400 hover:text-white" aria-label="QASight home">
                    <Icons.HomeIcon />
                  </Link>
                  QASight
                </li>
                {breadcrumbs(pathname).map((crumb, i, all) => (
                  <li key={`${crumb.label}-${i}`} className="flex items-center gap-1">
                    <span className="text-ink-500" aria-hidden>
                      /
                    </span>
                    {i === all.length - 1 ? (
                      <span className="font-semibold text-white" aria-current="page">
                        {crumb.label}
                      </span>
                    ) : crumb.to ? (
                      <Link to={crumb.to} className="hover:text-white">
                        {crumb.label}
                      </Link>
                    ) : (
                      <span>{crumb.label}</span>
                    )}
                  </li>
                ))}
              </ol>
            </nav>
            <div className="flex items-center gap-4">
              <DemoBadge />
              <UserBadge />
              <HealthDot />
            </div>
          </header>
          {/* At least a screen tall, so the footer starts below the fold and a page
              that fills in after loading doesn't push it down (layout shift). */}
          <main id="main" tabIndex={-1} className="min-h-screen outline-none">
            <Outlet />
          </main>
          <Footer />
        </div>
      </div>
    </div>
  );
}

function NavGroups({ size = "compact" }: { size?: "compact" | "touch" }) {
  return (
    <>
      {NAV_GROUPS.map((group) => (
        <div key={group.title ?? "home"} className="flex flex-col gap-1">
          {group.title ? (
            <p className={`mb-1 px-4 text-[11px] font-bold uppercase tracking-wider text-ink-400 ${size === "touch" ? "mt-3" : "mt-4"}`}>
              {group.title}
            </p>
          ) : null}
          {group.links.map((link) => (
            <NavItem key={link.to} link={link} size={size} />
          ))}
        </div>
      ))}
    </>
  );
}

function NavItem({ link, size = "compact" }: { link: NavLinkSpec; size?: "compact" | "touch" }) {
  const Icon = link.icon;
  const { pathname } = useLocation();
  const onSubPage = (link.also ?? []).some((to) => under(pathname, to));
  return (
    <NavLink
      to={link.to}
      end={link.end}
      className={({ isActive: exact }) => {
        const isActive = exact || onSubPage;
        return [
          "group flex items-center gap-3 rounded-2xl px-4 font-medium transition-colors",
          size === "touch" ? "py-2.5" : "py-2",
          isActive ? "bg-ink-800/80 text-white shadow-lg shadow-black/20" : "text-ink-300 hover:bg-ink-800/40 hover:text-white"
        ].join(" ");
      }}
    >
      {({ isActive: exact }) => (
        <>
          <span
            className={[
              "flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-xl transition-colors",
              exact || onSubPage ? "brand-tile" : "bg-ink-800 text-brand-400 shadow-md shadow-black/20 group-hover:bg-ink-700 group-hover:text-brand-300"
            ].join(" ")}
          >
            <Icon />
          </span>
          {link.label}
        </>
      )}
    </NavLink>
  );
}

/**
 * The sidebar's help card: a lit glass tile with the eye's green glow behind
 * it, pointing at the docs site.
 */
function HelpCard() {
  return (
    <div className="relative mt-auto overflow-hidden rounded-2xl border border-white/10 bg-ink-900 p-4">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            "radial-gradient(circle at 85% 0%, rgb(52 211 153 / 0.6), transparent 55%), radial-gradient(circle at 0% 100%, rgb(45 212 191 / 0.3), transparent 50%), radial-gradient(circle at 40% 60%, rgb(16 185 129 / 0.25), transparent 60%)"
        }}
      />
      <div className="relative">
        <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-white text-brand-500 shadow-md shadow-black/20">
          <Icons.HelpIcon />
        </span>
        <p className="mt-3 text-sm font-bold text-white">Need help?</p>
        <p className="text-xs text-ink-200">Guides, commands and CI setup.</p>
        <a
          href={DOCS_URL}
          target="_blank"
          rel="noreferrer"
          className="mt-3 block rounded-xl border border-white/15 bg-ink-950/50 px-3 py-2 text-center text-xs font-bold uppercase tracking-wider text-white backdrop-blur transition-colors hover:border-white/30 hover:bg-ink-950/70"
        >
          Docs
        </a>
        <Link to="/try" className="mt-2 block text-center text-xs font-semibold text-ink-200 hover:text-white hover:underline">
          Try it on a JUnit file
        </Link>
      </div>
    </div>
  );
}

const REPO_URL = "https://github.com/arieleven/qasight";

/** A quiet line under every page: where the docs and the code are. On phones it is the only Docs link. */
function Footer() {
  return (
    <footer className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t border-white/5 pt-5 text-xs text-ink-400 print:hidden">
      <span className="font-semibold text-ink-200">QASight</span>
      <span className="flex flex-wrap gap-4">
        <Link to="/try" className="hover:text-white">
          Try a file
        </Link>
        <a href={DOCS_URL} target="_blank" rel="noreferrer" className="hover:text-white">
          Docs
        </a>
        <a href={REPO_URL} target="_blank" rel="noreferrer" className="hover:text-white">
          GitHub
        </a>
        <a href={`${REPO_URL}/issues`} target="_blank" rel="noreferrer" className="hover:text-white">
          Report an issue
        </a>
      </span>
    </footer>
  );
}

const RELEASES_URL = "https://github.com/arieleven/qasight/releases";
const AUTHOR_URL = "https://github.com/arieleven";

/** The running version, and a link when PyPI has a newer one (GET /api/v1/version). */
export function VersionNote() {
  const query = useQuery({
    queryKey: ["version"],
    queryFn: () => apiClient.getVersion(),
    staleTime: 60 * 60 * 1000,
    retry: false
  });
  const info = query.data;
  if (!info) return null;
  return (
    <p className="mt-2 px-4 text-[11px] text-ink-400" data-testid="version-note">
      qasight {info.version}
      {info.update_available && info.latest ? (
        <>
          {" · "}
          <a href={RELEASES_URL} target="_blank" rel="noreferrer" className="font-semibold text-brand-300 hover:text-white">
            {info.latest} is out
          </a>
        </>
      ) : null}
      {" · made by "}
      <a href={AUTHOR_URL} target="_blank" rel="noreferrer" className="text-ink-300 underline-offset-2 hover:text-white hover:underline">
        arieleven
      </a>
    </p>
  );
}

/** Closes a popup on a mousedown outside `ref` or on Escape, which also returns focus to `trigger`. */
function useDismiss(
  open: boolean,
  close: () => void,
  ref: RefObject<HTMLElement | null>,
  trigger: RefObject<HTMLElement | null>
) {
  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      close();
      trigger.current?.focus();
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, close, ref, trigger]);
}

/**
 * Below the `lg` breakpoint the sidebar nav doesn't fit
 * next to the logo, so both collapse into one disclosure panel under the header.
 */
function MobileMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const { pathname } = useLocation();

  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, ref, buttonRef);

  // Close on navigation, including back/forward.
  useEffect(() => setOpen(false), [pathname]);

  return (
    <div
      ref={ref}
      className="lg:hidden"
      // Tabbing past the last link closes the panel instead of leaving it over the page.
      onBlur={(e) => {
        if (e.relatedTarget && !e.currentTarget.contains(e.relatedTarget)) close();
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-11 w-11 items-center justify-center rounded-xl text-ink-300 transition-colors hover:bg-ink-800 hover:text-white"
        aria-label="Menu"
        aria-expanded={open}
        aria-controls="mobile-nav"
      >
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
          <path d={open ? "M5 5l10 10M15 5L5 15" : "M3 6h14M3 10h14M3 14h14"} />
        </svg>
      </button>
      <nav
        id="mobile-nav"
        aria-label="Main"
        hidden={!open}
        // Covers a tap on the link for the page that is already open.
        onClick={(e) => {
          if ((e.target as Element).closest("a")) close();
        }}
        className="absolute inset-x-0 top-full border-b border-white/5 bg-ink-950 px-4 py-3 text-sm shadow-xl"
      >
        <div className="flex flex-col gap-1">
          <NavGroups size="touch" />
        </div>
      </nav>
    </div>
  );
}

/** Who is signed in, when the server runs with per-user accounts (TESTO_AUTH=accounts). */
function UserBadge() {
  const query = useQuery({
    queryKey: ["who-am-i"],
    queryFn: () => apiClient.getWhoAmI(),
    staleTime: 5 * 60_000,
    retry: false
  });
  const me = query.data;
  if (!me || me.auth_mode !== "accounts" || !me.signed_in) return null;
  return (
    <form method="post" action="/api/v1/auth/logout" className="flex items-center gap-2 text-xs text-ink-300" data-testid="user-badge">
      <span>
        <span className="font-semibold text-white">{me.username}</span> · {me.role}
      </span>
      <button type="submit" className="rounded-lg px-2 py-1 text-ink-300 hover:bg-ink-800/60 hover:text-white">
        Sign out
      </button>
    </form>
  );
}

function HealthDot() {
  const query = useQuery({
    queryKey: ["health-ready"],
    queryFn: () => apiClient.getHealthReady(),
    refetchInterval: 30_000,
    retry: false
  });

  const health = query.data;
  const ok = health?.status === "ready";
  const color = query.isError ? "bg-danger-400" : ok ? "bg-success-400" : health ? "bg-warn-400" : "bg-ink-500";
  const label = query.isError ? "API unreachable" : ok ? "API connected" : health ? "API degraded" : "Checking…";

  const detail = health?.checks
    ? Object.entries(health.checks)
        .map(([name, check]) => `${name}: ${check.status}${check.detail ? ` — ${check.detail}` : ""}`)
        .join("\n")
    : label;

  return (
    <span
      className="group relative flex items-center gap-1.5 text-xs text-ink-300"
      data-testid="health-indicator"
      title={detail}
    >
      <span className={`h-2.5 w-2.5 rounded-full ring-4 ring-white/5 ${color}`} aria-hidden />
      <span className="hidden sm:inline">{label}</span>
    </span>
  );
}
