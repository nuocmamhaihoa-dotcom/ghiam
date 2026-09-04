/**
 * ITY recording sync through ChốtKiểm `/api/sync/ity/*`.
 * Pulls call metadata for the last N days, then drives pending audio downloads.
 */

import { ckFetch } from "./chotKiemClient";

export type ItySyncRoundResult = {
  id: string;
  mode?: string;
  discovered: number;
  imported: number;
  skipped: number;
  failed: number;
  pendingBrowserDownload: number;
  remoteTotal: number;
  lastPage: number;
  hasMore: boolean;
  errors: string[];
};

export type ItyDownloadStatus = {
  ok: boolean;
  pendingCount: number;
  recordingKeyConfigured: boolean;
  serverCanDownload: boolean;
  recordingHostBlocked: boolean;
};

export type ItyPendingItem = {
  callId: string;
  phoneNumber?: string;
  fileName?: string;
  audioUrl?: string;
  createdAt?: number;
  externalId?: string;
};

export type ItySyncRoundInput = {
  daysBack?: number;
  startPage?: number;
  maxPages?: number;
  pageLimit?: number;
  maxImportPerRun?: number;
  autoAnalyze?: boolean;
};

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function normalizeRound(raw: Record<string, unknown>): ItySyncRoundResult {
  return {
    id: String(raw.id || ""),
    mode: typeof raw.mode === "string" ? raw.mode : undefined,
    discovered: num(raw.discovered),
    imported: num(raw.imported),
    skipped: num(raw.skipped),
    failed: num(raw.failed),
    pendingBrowserDownload: num(raw.pendingBrowserDownload),
    remoteTotal: num(raw.remoteTotal),
    lastPage: num(raw.lastPage, 1),
    hasMore: bool(raw.hasMore, false),
    errors: Array.isArray(raw.errors)
      ? raw.errors
          .map((e) => (typeof e === "string" ? e : JSON.stringify(e)))
          .slice(0, 10)
      : [],
  };
}

/** One paginated metadata sync round (default: last 10 days). */
export async function runItySyncRound(
  input: ItySyncRoundInput = {},
): Promise<ItySyncRoundResult> {
  // Keep each round small: full=true + large maxPages can hang while ChốtKiểm drain runs.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90_000);
  try {
    const raw = await ckFetch<Record<string, unknown>>("/api/sync/ity/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        daysBack: input.daysBack ?? 10,
        metadataOnly: true,
        autoAnalyze: input.autoAnalyze ?? false,
        full: true,
        startPage: input.startPage ?? 1,
        maxPages: input.maxPages ?? 1,
        pageLimit: input.pageLimit ?? 100,
        maxImportPerRun: input.maxImportPerRun ?? 100_000,
      }),
    });
    return normalizeRound(raw);
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("ITY sync-round timeout (>90s) — thử lại với maxPages=1");
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}


export async function fetchItyDownloadStatus(): Promise<ItyDownloadStatus> {
  const raw = await ckFetch<Record<string, unknown>>(
    "/api/sync/ity/download-status",
  );
  return {
    ok: bool(raw.ok, true),
    pendingCount: num(raw.pendingCount),
    recordingKeyConfigured: bool(raw.recordingKeyConfigured, false),
    serverCanDownload: bool(raw.serverCanDownload, false),
    recordingHostBlocked: bool(raw.recordingHostBlocked, false),
  };
}

export async function fetchItyPendingDownloads(limit = 20): Promise<{
  count: number;
  items: ItyPendingItem[];
}> {
  const capped = Math.max(1, Math.min(limit, 200));
  const raw = await ckFetch<{
    count?: number;
    items?: Array<Record<string, unknown>>;
  }>(`/api/sync/ity/pending-downloads?limit=${capped}`);

  const items = (raw.items || [])
    .map((item) => ({
      callId: String(item.callId || item.id || ""),
      phoneNumber:
        typeof item.phoneNumber === "string" ? item.phoneNumber : undefined,
      fileName: typeof item.fileName === "string" ? item.fileName : undefined,
      audioUrl: typeof item.audioUrl === "string" ? item.audioUrl : undefined,
      createdAt: num(item.createdAt, 0) || undefined,
      externalId:
        typeof item.externalId === "string" ? item.externalId : undefined,
    }))
    .filter((i) => i.callId);

  return { count: num(raw.count, items.length), items };
}

/** Start ChốtKiểm background proxy hunter for mass recording fetch. */
export async function startItyProxyHunt(): Promise<Record<string, unknown>> {
  return ckFetch<Record<string, unknown>>("/api/sync/ity/proxy-hunt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mass: true, background: true, massReach: true }),
  });
}

/** Process a small pending-download batch (slow — keep tiny). */
export async function processItyPendingDownloads(input: {
  callIds: string[];
  autoAnalyze?: boolean;
  concurrency?: number;
}): Promise<Record<string, unknown>> {
  if (!input.callIds.length) {
    return { ok: true, processed: 0, message: "Không có callId" };
  }

  return ckFetch<Record<string, unknown>>("/api/sync/ity/process-pending", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      callIds: input.callIds.slice(0, 10),
      autoAnalyze: input.autoAnalyze === true,
      concurrency: input.concurrency ?? 1,
    }),
  });
}

export async function fetchItySettingsSummary(): Promise<{
  accountCount: number;
  enabledAccounts: string[];
  daysBackSetting: number | null;
}> {
  const raw = await ckFetch<{
    accounts?: Array<{ username?: string; enabled?: boolean }>;
    daysBack?: number;
  }>("/api/sync/ity/settings");

  const accounts = raw.accounts || [];
  const enabled = accounts
    .filter((a) => a.enabled && a.username)
    .map((a) => String(a.username));

  return {
    accountCount: accounts.length,
    enabledAccounts: enabled,
    daysBackSetting: typeof raw.daysBack === "number" ? raw.daysBack : null,
  };
}
