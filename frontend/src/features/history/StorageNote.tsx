import { useQuery } from "@tanstack/react-query";

import { apiClient, type StorageResponse } from "../../lib/api-client";
import { IS_STATIC_BUILD } from "../../lib/static-backend";

export function formatBytes(value: number | null | undefined): string {
  if (value == null) return "unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${size} B` : `${size.toFixed(1)} ${units[unit]}`;
}

export function storageLine(storage: StorageResponse): string {
  const parts: string[] = [];
  if (storage.runs != null) parts.push(`${storage.runs.toLocaleString()} runs`);
  if (storage.database_bytes != null) parts.push(`database ${formatBytes(storage.database_bytes)}`);
  if (storage.history_files_bytes != null) parts.push(`files ${formatBytes(storage.history_files_bytes)}`);
  if (storage.report_archives) {
    parts.push(`${storage.report_archives.toLocaleString()} report archives (${formatBytes(storage.report_archive_bytes)})`);
  }
  if (storage.disk) parts.push(`${formatBytes(storage.disk.free_bytes)} free on disk (${Math.round(storage.disk.free_pct)}%)`);
  return parts.join(" · ");
}

/** How much the run history takes, under the Runs list, with a warning when the disk is low. */
export function StorageNote() {
  const query = useQuery({
    queryKey: ["storage"],
    queryFn: () => apiClient.getStorage(),
    staleTime: 60_000,
    // The demo snapshot has no server to measure.
    enabled: !IS_STATIC_BUILD
  });
  const storage = query.data;
  if (!storage) return null;
  const line = storageLine(storage);
  if (!line) return null;
  return (
    <div className="mt-4 text-xs text-ink-400" data-testid="storage-note">
      <p>
        <span className="font-medium text-ink-300">Storage:</span> {line}
      </p>
      {storage.disk?.low ? (
        <p className="mt-1 text-warn-300" role="status">
          The disk is getting full. Set history.retention (or TESTO_RETENTION_DAYS on a server) to delete old runs, or
          free some space.
        </p>
      ) : null}
    </div>
  );
}
