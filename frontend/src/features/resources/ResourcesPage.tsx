import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  apiClient,
  type ResourceActionItem,
  type ResourceItemStatus,
  type ResourceLeaseInfo,
  type ResourcePoolStatus
} from "../../lib/api-client";
import { Badge, Button, Card, EmptyState, PageHeader, Spinner } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { IS_STATIC_BUILD, loadManifest } from "../../lib/static-backend";

const EXAMPLE = `resources:
  flow-vms:
    items: [vm-01, vm-02, vm-03]
    lease_ttl: 120`;

/** "3m ago" from epoch seconds. */
function ago(seconds: number, now: number): string {
  const s = Math.max(0, Math.round(now - seconds));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function holderLine(lease: ResourceLeaseInfo): string {
  const h = lease.holder;
  const who = [h.user, h.host ? `@${h.host}` : ""].filter(Boolean).join(" ");
  const where = [h.run_id, h.stage, h.worker].filter(Boolean).join(" / ");
  return [who || "someone", where, h.note].filter(Boolean).join(" · ");
}

function KeyRow({ pool, item, now }: { pool: string; item: ResourceItemStatus; now: number }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const action = useMutation({
    mutationFn: (kind: "disable" | "enable" | "free") => {
      if (kind === "disable") return apiClient.disableResource(pool, item.item, "Disabled from the UI");
      if (kind === "enable") return apiClient.enableResource(pool, item.item);
      return apiClient.freeResource(pool, item.item);
    },
    onMutate: () => setError(null),
    onError: (err) => setError(err instanceof Error ? err.message : "That didn't work."),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["resources"] })
  });

  // Heartbeats stopped: the key stays out until the reuse delay is over.
  const expired = item.lease && item.lease.expires_at < now ? item.lease : null;
  const state = item.disabled ? (
    <Badge tone="danger">Disabled</Badge>
  ) : expired ? (
    <Badge tone="warn">Expired</Badge>
  ) : item.lease ? (
    <Badge tone="warn">Held</Badge>
  ) : (
    <Badge tone="success">Free</Badge>
  );

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm" data-testid="resource-key">
      <div className="grid min-w-0 gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono font-semibold text-white">{item.item}</span>
          {state}
          {item.labels.map((label) => (
            <Badge key={label}>{label}</Badge>
          ))}
        </div>
        {item.disabled ? (
          <p className="text-xs text-ink-300">
            {item.disabled_reason || "Taken out of the pool"}
            {item.disabled_by ? ` (by ${item.disabled_by})` : ""}
          </p>
        ) : item.lease ? (
          <p className="text-xs text-ink-300">
            {holderLine(item.lease)} · taken {ago(item.lease.taken_at, now)} · last heartbeat{" "}
            {ago(item.lease.heartbeat_at, now)}
            {expired
              ? ` · free again in ${Math.max(0, Math.ceil(expired.expires_at + (expired.reuse_delay_s ?? 0) - now))}s`
              : ""}
          </p>
        ) : null}
        {error ? (
          <p className="text-xs text-danger-400" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      {IS_STATIC_BUILD ? null : (
        <div className="flex gap-2">
          {item.lease ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={action.isPending}
              onClick={() => action.mutate("free")}
              aria-label={`Take ${item.item} back from its holder`}
            >
              Take back
            </Button>
          ) : null}
          {item.disabled ? (
            <Button size="sm" variant="secondary" disabled={action.isPending} onClick={() => action.mutate("enable")}>
              Put back
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              disabled={action.isPending}
              onClick={() => action.mutate("disable")}
              aria-label={`Take ${item.item} out of the pool`}
            >
              Disable
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function PoolCard({ pool, now }: { pool: ResourcePoolStatus; now: number }) {
  const held = pool.items.filter((item) => item.lease).length;
  return (
    <Card
      title={pool.name}
      actions={
        <span className="text-xs text-ink-300" data-testid="pool-summary">
          {pool.free} free · {held} held of {pool.items.length}
          {pool.waiting ? ` · ${pool.waiting} waiting` : ""}
        </span>
      }
    >
      <ul className="-mx-5 -mb-5 divide-fade">
        {pool.items.map((item) => (
          <KeyRow key={item.item} pool={pool.name} item={item} now={now} />
        ))}
      </ul>
    </Card>
  );
}

/** Create and destroy commands people ran with `qasight pool ... --apply`. QASight never runs them itself. */
function ProviderLog({ actions, now }: { actions: ResourceActionItem[]; now: number }) {
  return (
    <Card
      title="Provider commands"
      actions={<span className="text-xs text-ink-300">Run by people with qasight pool, newest first</span>}
    >
      <ul className="-mx-5 -mb-5 divide-fade" data-testid="provider-log">
        {actions.map((a, i) => (
          <li key={`${a.at}-${a.item}-${i}`} className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm">
            <Badge tone={a.ok ? "success" : "danger"}>{a.verb}</Badge>
            <span className="font-mono font-semibold text-white">
              {a.pool}/{a.item}
            </span>
            <span className="text-xs text-ink-300">
              {[a.user, a.host ? `@${a.host}` : ""].filter(Boolean).join(" ") || "someone"} · {ago(a.at, now)}
              {a.ok ? "" : ` · failed: ${a.detail || "no details"}`}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Every resource pool, who holds each key, and how many requests wait (refreshes every 5 seconds). */
export function ResourcesPage() {
  const query = useQuery({
    queryKey: ["resources"],
    queryFn: () => apiClient.listResources(),
    refetchInterval: IS_STATIC_BUILD ? false : 5_000
  });
  // The demo site has no provider log to show.
  const actions = useQuery({
    queryKey: ["resource-actions"],
    queryFn: () => apiClient.listResourceActions(),
    enabled: !IS_STATIC_BUILD,
    refetchInterval: 30_000
  });
  // The demo site is a snapshot: times are "ago" from when it was taken, not from now.
  const manifest = useQuery({
    queryKey: ["static-manifest"],
    queryFn: loadManifest,
    enabled: IS_STATIC_BUILD,
    staleTime: Infinity
  });

  if (query.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-ink-300">
        <Spinner /> Loading resource pools...
      </div>
    );
  }
  if (query.isError) {
    return <p className="text-sm text-danger-400">Couldn't load resource pools. Check that the API is running, then reload.</p>;
  }

  const pools = query.data?.pools ?? [];
  const snapshotAt = manifest.data?.generated_at ?? null;
  const now = snapshotAt ?? query.dataUpdatedAt / 1000;
  return (
    <section>
      <PageHeader
        title="Resources"
        subtitle="Keys that parallel workers share, like VMs, devices or test accounts. Each worker gets its own."
      />
      {IS_STATIC_BUILD && pools.length > 0 ? (
        <p className="mb-4 text-sm text-ink-300" data-testid="resources-snapshot-note">
          These pools and holders are made up (<code>qasight lease demo</code>), as they were
          {snapshotAt ? ` on ${new Date(snapshotAt * 1000).toUTCString()}` : " when this demo was built"}.
        </p>
      ) : null}
      {pools.length === 0 ? (
        <EmptyState
          mascot={<MuscleShrug />}
          title="No resource pools yet"
          message={
            <>
              <p>
                List your keys under <code>resources:</code> in qasight.yaml, then run each worker with{" "}
                <code>qasight lease run POOL -- your-command</code>. It gets its key in <code>TESTO_RESOURCE_ID</code>.
              </p>
              <p className="mt-2">
                Just looking? <code>qasight lease demo</code> fills this page with made-up pools and holders.
              </p>
              <pre className="mt-3 overflow-x-auto rounded-xl bg-ink-950/80 p-3 text-left text-xs text-ink-200">{EXAMPLE}</pre>
            </>
          }
        />
      ) : (
        <div className="grid gap-4">
          {pools.map((pool) => (
            <PoolCard key={pool.name} pool={pool} now={now} />
          ))}
          {actions.data?.actions?.length ? <ProviderLog actions={actions.data.actions} now={now} /> : null}
        </div>
      )}
    </section>
  );
}
