/**
 * Tracks ITY recording download throughput by sampling pending counts over time.
 * Persists under data/ity-download-progress.json for the dashboard.
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
  samples: ProgressSample[];
  events: ProgressEvent[];
};

const DATA_DIR = path.join(process.cwd(), "data");
const STORE_PATH = path.join(DATA_DIR, "ity-download-progress.json");
const SAMPLE_LIMIT = 120;
const EVENT_LIMIT = 80;
const WINDOW_MS = 10 * 60_000;

async function readStore(): Promise<Store> {
  try {
    const raw = await fs.readFile(STORE_PATH, "utf8");
    return JSON.parse(raw) as Store;
  } catch {
    return {
      updatedAt: new Date(0).toISOString(),
      phase: "idle",
      message: "Chưa có dữ liệu tốc độ tải",
      startedAt: null,
      startPending: null,
      samples: [],
      events: [],
    };
  }
}

async function writeStore(store: Store) {
  await fs.mkdir(DATA_DIR, { recursive: true });
  await fs.writeFile(STORE_PATH, JSON.stringify(store, null, 2), "utf8");
}

function computeRate(samples: ProgressSample[]): {
  filesPerMinute: number;
  etaMinutes: number | null;
  windowMinutes: number;
} {
  if (samples.length < 2) {
    return { filesPerMinute: 0, etaMinutes: null, windowMinutes: 0 };
  }
  const newest = samples[samples.length - 1]!;
  const newestTs = Date.parse(newest.at);
  const windowStart = newestTs - WINDOW_MS;
  const inWindow = samples.filter((s) => Date.parse(s.at) >= windowStart);
  const first = inWindow[0] ?? samples[0]!;
  const last = inWindow[inWindow.length - 1] ?? newest;
  const elapsedMin = Math.max(
    (Date.parse(last.at) - Date.parse(first.at)) / 60_000,
    1 / 60,
  );
  const dropped = first.pendingCount - last.pendingCount;
  const filesPerMinute = dropped > 0 ? dropped / elapsedMin : 0;
  const etaMinutes =
    filesPerMinute > 0.05
      ? Math.ceil(last.pendingCount / filesPerMinute)
      : null;
  return {
    filesPerMinute: Math.round(filesPerMinute * 10) / 10,
    etaMinutes,
    windowMinutes: Math.round(elapsedMin * 10) / 10,
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

  const sample: ProgressSample = {
    at: now,
    pendingCount,
    livingCount,
    drainRunning: Boolean(proxy?.drainRunning),
    hunterRunning: Boolean(proxy?.hunterRunning),
  };

  store.samples = [...store.samples, sample].slice(-SAMPLE_LIMIT);
  store.updatedAt = now;

  if (
    !store.startedAt ||
    (store.startPending != null && pendingCount > store.startPending + 50)
  ) {
    store.startedAt = now;
    store.startPending = pendingCount;
  }

  if (options?.phase) store.phase = options.phase;
  else if (pendingCount === 0) store.phase = "idle";
  else if (proxy?.drainRunning) store.phase = "draining";
  else store.phase = store.phase === "idle" ? "queued" : store.phase;

  if (options?.message) store.message = options.message;
  else if (pendingCount === 0) store.message = "Hàng chờ tải ghi âm trống";
  else {
    store.message = `Đang tải · còn ~${pendingCount} pending · proxy sống ${livingCount}`;
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

  const rate = computeRate(store.samples);
  const completedSinceStart =
    store.startPending != null
      ? Math.max(0, store.startPending - pendingCount)
      : 0;

  return {
    updatedAt: now,
    phase: store.phase,
    message: store.message,
    pendingCount,
    livingCount,
    drainRunning: Boolean(proxy?.drainRunning),
    hunterRunning: Boolean(proxy?.hunterRunning),
    filesPerMinute: rate.filesPerMinute,
    etaMinutes: rate.etaMinutes,
    completedSinceStart,
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
