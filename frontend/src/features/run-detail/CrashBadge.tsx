import { Badge } from "../../components/ui";

// Kinds the API reports (qasight/engine/crash_signatures.py).
const CRASH_LABELS: Record<string, { label: string; title: string }> = {
  stack_overflow: {
    label: "Stack overflow",
    title: "The output shows a stack overflow, usually endless recursion"
  },
  crash: {
    label: "Crashed",
    title: "The process crashed (for example a segmentation fault) instead of failing an assertion"
  }
};

/** A red badge when a stage or test died of a stack overflow or a crash; nothing otherwise. */
export function CrashBadge({ kind }: { kind?: string | null }) {
  const crash = kind ? CRASH_LABELS[kind] : undefined;
  if (!crash) {
    return null;
  }
  return (
    <Badge tone="danger" title={crash.title}>
      {crash.label}
    </Badge>
  );
}
