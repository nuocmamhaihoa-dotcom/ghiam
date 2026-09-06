/**
 * Continuous ITY proxy pool health — reads ChốtKiểm hunter/drain status,
 * auto-kicks mass proxy-hunt when living proxies drop below threshold,
 * and persists snapshots for the UI.
 */

import { promises as fs } from "fs";
import path from "path";
import { ckFetch } from "./chotKiemClient";
import { startItyProxyHunt } from "./itySyncClient";

export type ProxyPoolSnapshot = {
  ok: boolean;
  livingCount: number;
  poolSize: number;
  configuredCount: number;
  deadCount: number;
  minLiving: number;
  hunterRunning: boolean;
  hunterEnabled: boolean;
  nightWindowActive: boolean;
  nightWindowLabel: string | null;
  drainRunning: boolean;
  drainEnabled: boolean;
  pendingDownloads: number;
  lastHuntAt: number | null;
  lastCheckAt: number | null;
  lastAdded: number;
  lastTested: number;
  lastSourcesOk: number;
  lastSourcesTried: number;
  lastCandidates: number;
  lastError: string | null;
  lastReason: string | null;
  hint: string | null;
  sampleProxies: string[];
  fetchedAt: string;
  autoEnsured: boolean;
  ensureAction: "none" | "hunted" | "skipped";
};

export type ProxyHistoryPoint = {
  at: string;
  livingCount: number;
  poolSize: number;
  pendingDownloads: number;
};

export type ProxyStore = {
  updatedAt: string;
  latest: ProxyPoolSnapshot | null;
  history: ProxyHistoryPoint[];
};

const DATA_DIR = path.join(process.cwd(), "data");
const STORE_PATH = path.join(DATA_DIR, "proxy-pool.json");
const DEFAULT_MIN_LIVING = 20;
const HISTORY_LIMIT = 180;

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function maskProxy(proxy: string): string {
  return proxy.replace(/\/\/([^/@]+)@/, "//***@");
}

async function ensureDir() {
  await fs.mkdir(DATA_DIR, { recursive: true });
}

async function readStore(): Promise<ProxyStore> {
  try {
    const raw = await fs.readFile(STORE_PATH, "utf8");
    return JSON.parse(raw) as ProxyStore;
  } catch {
    return { updatedAt: new Date(0).toISOString(), latest: null, history: [] };
  }
}

async function writeStore(store: ProxyStore) {
  await ensureDir();
  await fs.writeFile(STORE_PATH, JSON.stringify(store, null, 2), "utf8");
}

/** Pull hunter / proxy / drain status from ChốtKiểm download-status. */
export async function fetchProxyPoolStatus(): Promise<ProxyPoolSnapshot> {
  const raw = await ckFetch<Record<string, unknown>>(
    "/api/sync/ity/download-status",
  );
  const hunter = (raw.hunter || {}) as Record<string, unknown>;
  const proxy = (raw.proxy || {}) as Record<string, unknown>;
  const drain = (raw.drain || {}) as Record<string, unknown>;

  const configured = Array.isArray(proxy.configured)
    ? proxy.configured.filter((p): p is string => typeof p === "string")
    : [];
  const livingList = Array.isArray(proxy.living)
    ? proxy.living.filter((p): p is string => typeof p === "string")
    : [];

  const livingCount = Math.max(num(hunter.livingCount), livingList.length);
  const poolSize = Math.max(
    num(hunter.poolSize),
    configured.length,
    livingCount,
  );

  return {
    ok: bool(raw.ok, true),
    livingCount,
    poolSize,
    configuredCount: configured.length,
    deadCount: num(proxy.deadCount),
    minLiving: num(hunter.minLiving, DEFAULT_MIN_LIVING),
    hunterRunning: bool(hunter.running),
    hunterEnabled: bool(hunter.enabled, true),
    nightWindowActive: bool(hunter.nightWindowActive),
    nightWindowLabel: str(hunter.nightWindowLabel),
    drainRunning: bool(drain.running),
    drainEnabled: bool(drain.enabled, true),
    pendingDownloads: num(raw.pendingCount ?? drain.lastPending),
    lastHuntAt: num(hunter.lastHuntAt) || null,
    lastCheckAt: num(hunter.lastCheckAt) || null,
    lastAdded: num(hunter.lastAdded),
    lastTested: num(hunter.lastTested),
    lastSourcesOk: num(hunter.lastSourcesOk),
    lastSourcesTried: num(hunter.lastSourcesTried),
    lastCandidates: num(hunter.lastCandidates),
    lastError: str(hunter.lastError),
    lastReason: str(hunter.lastReason),
    hint: str(raw.hint),
    sampleProxies: (livingList.length ? livingList : configured)
      .slice(0, 16)
      .map(maskProxy),
    fetchedAt: new Date().toISOString(),
    autoEnsured: false,
    ensureAction: "none",
  };
}

/**
 * Always-on maintenance: if living proxies < minLiving (or hunter idle while
 * pending downloads remain), kick mass proxy-hunt and persist snapshot.
 */
export async function ensureProxyPool(options?: {
  minLiving?: number;
  forceHunt?: boolean;
}): Promise<ProxyPoolSnapshot> {
  const minLiving = options?.minLiving ?? DEFAULT_MIN_LIVING;
  let snapshot = await fetchProxyPoolStatus();
  snapshot.minLiving = minLiving;

  const needsHunt =
    options?.forceHunt === true ||
    snapshot.livingCount < minLiving ||
    (!snapshot.hunterRunning && snapshot.pendingDownloads > 0);

  if (needsHunt) {
    try {
      await startItyProxyHunt();
      snapshot = await fetchProxyPoolStatus();
      snapshot.minLiving = minLiving;
      snapshot.autoEnsured = true;
      snapshot.ensureAction = "hunted";
    } catch (error) {
      snapshot.autoEnsured = true;
      snapshot.ensureAction = "hunted";
      snapshot.lastError =
        error instanceof Error ? error.message : "proxy-hunt failed";
    }
  } else {
    snapshot.ensureAction = "skipped";
  }

  const store = await readStore();
  store.latest = snapshot;
  store.updatedAt = snapshot.fetchedAt;
  store.history = [
    ...store.history,
    {
      at: snapshot.fetchedAt,
      livingCount: snapshot.livingCount,
      poolSize: snapshot.poolSize,
      pendingDownloads: snapshot.pendingDownloads,
    },
  ].slice(-HISTORY_LIMIT);
  await writeStore(store);

  return snapshot;
}

export async function getProxyPoolStore(): Promise<ProxyStore> {
  return readStore();
}
