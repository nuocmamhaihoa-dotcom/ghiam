/**
 * Tracks ITY recording download throughput by sampling pending counts over time.
 * Persists under data/ity-download-progress.json for the dashboard.
 *
 * Rate is robust to pending spikes (new metadata sync can raise the queue):
 * we accumulate completed files on every pending drop and compute
 * lifetime + recent-window rates.
 */

import { promises as fs } from "fs";
import path from "path";
import { fetchItyDownloadStatus } from "./itySyncClient";
import { fetchProxyPoolStatus } from "./proxyPool";
import { getLibraryStats } from "./recordingLibrary";

export type ProgressSample = {
  at: string;
  pendingCount: number;
  livingCount: number;
  drainRunning: boolean;
  hunterRunning: boolean;
  /** Monotonic completed count since tracking started (ignores pending spikes). */
  completedTotal: number;
};

export type ProgressEvent = {
  at: string;
  phase: string;
  message: string;
  pendingCount?: number;
  delta?: number;
};

export type DownloadProgressSnapshot = {
  updatedAt: string;
  phase: string;
  message: string;
  pendingCount: number;
  livingCount: number;
  drainRunning: boolean;
  hunterRunning: boolean;
  filesPerMinute: number;
  /** Lifetime average since tracking started. */
  lifetimeFilesPerMinute: number;
  etaMinutes: number | null;
  completedSinceStart: number;
  windowMinutes: number;
  libraryTotal: number;
  libraryWithAudio: number;
  samples: ProgressSample[];
  events: ProgressEvent[];
};

type Store = {
  updatedAt: string;
  phase: string;
  message: string;
  startedAt: string | null;
  startPending: number | null;
  /** Running total of pending drops (never decreases). */
  completedTotal: number;
  lastPending: number | null;
  samples: ProgressSample[];
  events: ProgressEvent[];
};

const DATA_DIR = path.join(process.cwd(), "data");
const STORE_PATH = path.join(DATA_DIR, "ity-download-progress.json");
const SAMPLE_LIMIT = 120;
const EVENT_LIMIT = 80;
const WINDOW_MS = 15 * 60_000;

async function readStore(): Promise<Store> {
  try {
    const raw = await fs.readFile(STORE_PATH, "utf8");
    const parsed = JSON.parse(raw) as Partial<Store>;
    return {
      updatedAt: parsed.updatedAt || new Date(0).toISOString(),
      phase: parsed.phase || "idle",
      message: parsed.message || "Chưa có dữ liệu tốc độ tải",
      startedAt: parsed.startedAt ?? null,
      startPending: parsed.startPending ?? null,
      completedTotal: Number(parsed.completedTotal) || 0,
      lastPending:
        typeof parsed.lastPending === "number" ? parsed.lastPending : null,
      samples: Array.isArray(parsed.samples) ? parsed.samples : [],
      events: Array.isArray(parsed.events) ? parsed.events : [],
    };
  } catch {
    return {
      updatedAt: new Date(0).toISOString(),
      phase: "idle",
      message: "Chưa có dữ liệu tốc độ tải",
      startedAt: null,
      startPending: null,
      completedTotal: 0,
      lastPending: null,
      samples: [],
      events: [],
    };
  }
}

async function writeStore(store: Store) {
  await fs.mkdir(DATA_DIR, { recursive: true });
  await fs.writeFile(STORE_PATH, JSON.stringify(store, null, 2), "utf8");
}

function rateFromCompleted(
  older: ProgressSample,
  newer: ProgressSample,
): number {
  const elapsedMin = Math.max(
    (Date.parse(newer.at) - Date.parse(older.at)) / 60_000,
    1 / 60,
  );
  const gained = newer.completedTotal - older.completedTotal;
  return gained > 0 ? gained / elapsedMin : 0;
}

function computeRate(
  samples: ProgressSample[],
  pendingCount: number,
  startedAt: string | null,
  completedTotal: number,
): {
  filesPerMinute: number;
  lifetimeFilesPerMinute: number;
  etaMinutes: number | null;
  windowMinutes: number;
} {
  if (samples.length < 2) {
    return {
      filesPerMinute: 0,
      lifetimeFilesPerMinute: 0,
      etaMinutes: null,
      windowMinutes: 0,
    };
  }

  const newest = samples[samples.length - 1]!;
  const newestTs = Date.parse(newest.at);
  const windowStart = newestTs - WINDOW_MS;
  const inWindow = samples.filter((s) => Date.parse(s.at) >= windowStart);
  const firstInWindow = inWindow[0] ?? samples[0]!;
  const windowRate = rateFromCompleted(firstInWindow, newest);
  const windowMinutes = Math.round(
    Math.max(
      (Date.parse(newest.at) - Date.parse(firstInWindow.at)) / 60_000,
      0,
    ) * 10,
  ) / 10;

  let lifetimeFilesPerMinute = 0;
  if (startedAt && completedTotal > 0) {
    const lifeMin = Math.max(
      (newestTs - Date.parse(startedAt)) / 60_000,
      1 / 60,
    );
    lifetimeFilesPerMinute = completedTotal / lifeMin;
  }

  // Prefer recent window when it has signal; otherwise fall back to lifetime.
  const filesPerMinute =
    windowRate > 0.05
      ? windowRate
      : lifetimeFilesPerMinute > 0.05
        ? lifetimeFilesPerMinute
        : 0;

  const etaMinutes =
    filesPerMinute > 0.05
      ? Math.ceil(pendingCount / filesPerMinute)
      : null;

  return {
    filesPerMinute: Math.round(filesPerMinute * 10) / 10,
    lifetimeFilesPerMinute: Math.round(lifetimeFilesPerMinute * 10) / 10,
    etaMinutes,
    windowMinutes,
  };
}

/** Sample live pending/proxy state and append to progress history. */
export async function sampleDownloadProgress(options?: {
  phase?: string;
  message?: string;
  event?: string;
}): Promise<DownloadProgressSnapshot> {
  const [download, proxy, library] = await Promise.all([
    fetchItyDownloadStatus().catch(() => null),
    fetchProxyPoolStatus().catch(() => null),
    getLibraryStats().catch(() => null),
  ]);

  const store = await readStore();
  const pendingCount =
    download?.pendingCount ?? store.samples.at(-1)?.pendingCount ?? 0;
  const livingCount = proxy?.livingCount ?? 0;
  const now = new Date().toISOString();

  // Bootstrap completedTotal from legacy samples (upgrade path from old store).
  if (store.completedTotal === 0 && store.samples.length >= 2) {
    let peak = store.samples[0]!.pendingCount;
    let gained = 0;
    for (const s of store.samples) {
      if (s.pendingCount > peak) peak = s.pendingCount;
      else if (s.pendingCount < peak) {
        gained += peak - s.pendingCount;
        peak = s.pendingCount;
      }
    }
    if (gained > 0) {
      store.completedTotal = gained;
      if (!store.startedAt) {
        store.startedAt = store.samples[0]!.at;
        store.startPending = store.samples[0]!.pendingCount;
      }
    }
  }

  // Accumulate completions on pending drops; ignore increases (new metadata).
  if (store.lastPending != null && pendingCount < store.lastPending) {
    store.completedTotal += store.lastPending - pendingCount;
  } else if (
    store.lastPending == null &&
    store.samples.length > 0 &&
    pendingCount < store.samples[store.samples.length - 1]!.pendingCount
  ) {
    store.completedTotal +=
      store.samples[store.samples.length - 1]!.pendingCount - pendingCount;
  }
  store.lastPending = pendingCount;

  if (!store.startedAt) {
    store.startedAt = now;
    store.startPending = pendingCount;
  }

  const sample: ProgressSample = {
    at: now,
    pendingCount,
    livingCount,
    drainRunning: Boolean(proxy?.drainRunning),
    hunterRunning: Boolean(proxy?.hunterRunning),
    completedTotal: store.completedTotal,
  };

  store.samples = [...store.samples, sample].slice(-SAMPLE_LIMIT);
  store.updatedAt = now;

  if (options?.phase) store.phase = options.phase;
  else if (pendingCount === 0) store.phase = "idle";
  else if (proxy?.drainRunning) store.phase = "draining";
  else store.phase = store.phase === "idle" ? "queued" : store.phase;

  const ratePreview = computeRate(
    store.samples,
    pendingCount,
    store.startedAt,
    store.completedTotal,
  );

  if (options?.message) store.message = options.message;
  else if (pendingCount === 0) store.message = "Hàng chờ tải ghi âm trống";
  else {
    const speedLabel =
      ratePreview.filesPerMinute > 0
        ? `${ratePreview.filesPerMinute} file/phút`
        : "đang đo tốc độ";
    store.message = `Đang tải · còn ~${pendingCount} pending · ${speedLabel} · proxy sống ${livingCount}`;
  }

  if (options?.event) {
    const prev =
      store.samples.length >= 2
        ? store.samples[store.samples.length - 2]
        : null;
    store.events = [
      ...store.events,
      {
        at: now,
        phase: store.phase,
        message: options.event,
        pendingCount,
        delta: prev ? prev.pendingCount - pendingCount : 0,
      },
    ].slice(-EVENT_LIMIT);
  }

  await writeStore(store);

  const rate = computeRate(
    store.samples,
    pendingCount,
    store.startedAt,
    store.completedTotal,
  );

  return {
    updatedAt: now,
    phase: store.phase,
    message: store.message,
    pendingCount,
    livingCount,
    drainRunning: Boolean(proxy?.drainRunning),
    hunterRunning: Boolean(proxy?.hunterRunning),
    filesPerMinute: rate.filesPerMinute,
    lifetimeFilesPerMinute: rate.lifetimeFilesPerMinute,
    etaMinutes: rate.etaMinutes,
    completedSinceStart: store.completedTotal,
    windowMinutes: rate.windowMinutes,
    libraryTotal: library?.total ?? 0,
    libraryWithAudio: library?.withAudio ?? 0,
    samples: store.samples.slice(-40),
    events: store.events.slice(-30),
  };
}

export async function getDownloadProgress(): Promise<DownloadProgressSnapshot> {
  return sampleDownloadProgress();
}

export async function noteDownloadEvent(
  phase: string,
  message: string,
): Promise<DownloadProgressSnapshot> {
  return sampleDownloadProgress({ phase, message, event: message });
}
