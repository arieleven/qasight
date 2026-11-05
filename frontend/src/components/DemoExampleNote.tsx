import { useQuery } from "@tanstack/react-query";

import { IS_STATIC_BUILD, loadManifest } from "../lib/static-backend";

/** The demo's manifest, shared by every component that asks (one fetch). */
export function useStaticManifest() {
  return useQuery({ queryKey: ["static-manifest"], queryFn: loadManifest, enabled: IS_STATIC_BUILD, staleTime: Infinity });
}

/** True when the demo export filled `page` with made-up examples. */
export function useIsDemoExample(page: string): boolean {
  const manifest = useStaticManifest();
  return Boolean(manifest.data?.examples?.includes(page));
}

/**
 * Says plainly that what follows is made up: the live demo has no AI key and
 * records no releases, so it shows examples built from its runs instead.
 */
export function DemoExampleNote({ what, className, testId = "demo-example-note" }: { what: string; className?: string; testId?: string }) {
  return (
    <p
      role="note"
      data-testid={testId}
      className={`rounded-xl border border-warn-400/40 bg-warn-500/10 px-3 py-2 text-xs text-ink-100 ${className ?? ""}`}
    >
      <strong className="font-semibold text-warn-300">Fake example for the live demo.</strong> {what}
    </p>
  );
}
