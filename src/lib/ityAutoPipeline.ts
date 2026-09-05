import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { ensureProxyPool } from "@/lib/proxyPool";
import {
  fetchItyDownloadStatus,
  fetchItyPendingDownloads,
  processItyPendingDownloads,
  runItySyncRound,
  startItyProxyHunt,
} from "@/lib/itySyncClient";
import {
  noteDownloadEvent,
  sampleDownloadProgress,
} from "@/lib/ityDownloadProgress";
import {
  getLibraryStats,
  analyzePendingRecordings,
  reanalyzeRecordings,
  syncRecordingsFromChotKiem,
} from "@/lib/recordingLibrary";

const DATA_DIR = path.join(process.cwd(), "data");
const STATE_PATH = path.join(DATA_DIR, "ity-auto-pipeline.json");

export type AutoPipelineConfig = {
  /** Seconds between worker ticks. */
  intervalSec: number;
  /** Pending callIds per round (sync client hard-caps at 8). */
  processBatchSize: number;
  /** Process-pending rounds inside one tick. */
  processRounds: number;
  importLimit: number;
  minLivingProxies: number;
  syncEveryTicks: number;
  importEveryTicks: number;
  reanalyzeEveryTicks: number;
};

export type AutoPipelineState = {
  enabled: boolean;
  tickCount: number;
  lastTickAt: string | null;
  lastError: string | null;
  lastSummary: string | null;
  consecutiveErrors: number;
  startedAt: string | null;
  config: AutoPipelineConfig;
  stats: {
    processRuns: number;
    filesProcessed: number;
    syncRuns: number;
    importRuns: number;
    imported: number;
    reanalyzeRuns: number;
    reanalyzed: number;
  };
};

const DEFAULT_CONFIG: AutoPipelineConfig = {
  intervalSec: 45,
  processBatchSize: 8,
  processRounds: 5,
  importLimit: 80,
  minLivingProxies: 40,
  syncEveryTicks: 8,
  importEveryTicks: 1,
  reanalyzeEveryTicks: 5,
};

const DEFAULT_STATE: AutoPipelineState = {
  enabled: false,
  tickCount: 0,
  lastTickAt: null,
  lastError: null,
  lastSummary: null,
  consecutiveErrors: 0,
  startedAt: null,
  config: DEFAULT_CONFIG,
  stats: {
    processRuns: 0,
    filesProcessed: 0,
    syncRuns: 0,
    importRuns: 0,
    imported: 0,
    reanalyzeRuns: 0,
    reanalyzed: 0,
  },
};

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function pickNumber(...values: unknown[]): number {
  for (const value of values) {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return 0;
}

async function loadState(): Promise<AutoPipelineState> {
  try {
    const raw = await readFile(STATE_PATH, "utf8");
    const parsed = JSON.parse(raw) as Partial<AutoPipelineState>;
    return {
      ...DEFAULT_STATE,
      ...parsed,
      config: { ...DEFAULT_CONFIG, ...(parsed.config || {}) },
      stats: { ...DEFAULT_STATE.stats, ...(parsed.stats || {}) },
    };
  } catch {
    return {
      ...DEFAULT_STATE,
      config: { ...DEFAULT_CONFIG },
      stats: { ...DEFAULT_STATE.stats },
    };
  }
}

async function saveState(state: AutoPipelineState): Promise<void> {
  await mkdir(DATA_DIR, { recursive: true });
  await writeFile(STATE_PATH, JSON.stringify(state, null, 2), "utf8");
}

export async function getAutoPipelineState(): Promise<AutoPipelineState> {
  return loadState();
}

export async function setAutoPipelineEnabled(
  enabled: boolean,
): Promise<AutoPipelineState> {
  const state = await loadState();
  state.enabled = enabled;
  if (enabled) state.startedAt = new Date().toISOString();
  state.lastError = null;
  state.consecutiveErrors = 0;
  state.lastSummary = enabled
    ? "Đã bật pipeline tự động tải + phân tích."
    : "Đã tắt pipeline tự động.";
  await saveState(state);
  return state;
}

export async function updateAutoPipelineConfig(
  patch: Partial<AutoPipelineConfig>,
): Promise<AutoPipelineState> {
  const state = await loadState();
  state.config = {
    intervalSec: clamp(Number(patch.intervalSec ?? state.config.intervalSec), 20, 300),
    processBatchSize: clamp(
      Number(patch.processBatchSize ?? state.config.processBatchSize),
      1,
      8,
    ),
    processRounds: clamp(
      Number(patch.processRounds ?? state.config.processRounds),
      1,
      8,
    ),
    importLimit: clamp(Number(patch.importLimit ?? state.config.importLimit), 10, 150),
    minLivingProxies: clamp(
      Number(patch.minLivingProxies ?? state.config.minLivingProxies),
      10,
      120,
    ),
    syncEveryTicks: clamp(
      Number(patch.syncEveryTicks ?? state.config.syncEveryTicks),
      2,
      40,
    ),
    importEveryTicks: clamp(
      Number(patch.importEveryTicks ?? state.config.importEveryTicks),
      1,
      20,
    ),
    reanalyzeEveryTicks: clamp(
      Number(patch.reanalyzeEveryTicks ?? state.config.reanalyzeEveryTicks),
      1,
      40,
    ),
  };
  await saveState(state);
  return state;
}

/**
 * One continuous cycle:
 * 1) ensure proxies + hunt
 * 2) process pending ITY audio downloads
 * 3) periodically sync metadata / import+analyze / reanalyze
 */
export async function runAutoPipelineTick(force = false): Promise<{
  state: AutoPipelineState;
  skipped: boolean;
  result: Record<string, unknown>;
}> {
  const state = await loadState();
  if (!state.enabled && !force) {
    return {
      state,
      skipped: true,
      result: {
        reason: "disabled",
        message: "Pipeline đang tắt — bật để chạy liên tục.",
      },
    };
  }

  const tickStarted = Date.now();
  const result: Record<string, unknown> = {};

  try {
    const proxySnap = await ensureProxyPool({
      minLiving: state.config.minLivingProxies,
    });
    result.proxyLiving = proxySnap.livingCount;
    result.proxyPoolSize = proxySnap.poolSize;
    result.proxyAction = proxySnap.ensureAction;

    const hunt = await startItyProxyHunt().catch((error) => ({
      ok: false,
      error: error instanceof Error ? error.message : "proxy hunt failed",
    }));
    result.proxyHunt = hunt;

    let processed = 0;
    const roundResults: Array<Record<string, unknown>> = [];
    for (let round = 0; round < state.config.processRounds; round += 1) {
      const pending = await fetchItyPendingDownloads(
        state.config.processBatchSize,
      ).catch(() => ({ count: 0, items: [] as Array<{ callId: string }> }));
      const callIds = pending.items.map((item) => item.callId).filter(Boolean);
      if (!callIds.length) break;

      try {
        const processedPayload = await processItyPendingDownloads({
          callIds,
          concurrency: 3,
          timeoutMs: 45_000,
          autoAnalyze: false,
        });
        const data = asRecord(processedPayload);
        const n = pickNumber(
          data.processed,
          data.completed,
          data.downloaded,
          callIds.length,
        );
        processed += n;
        roundResults.push({ round: round + 1, ok: true, processed: n });
      } catch (error) {
        roundResults.push({
          round: round + 1,
          ok: false,
          error: error instanceof Error ? error.message : "process failed",
          batch: callIds.length,
        });
        // Timeouts are expected — ChốtKiểm drain keeps running in background.
        break;
      }
    }

    state.stats.processRuns += 1;
    state.stats.filesProcessed += processed;
    await noteDownloadEvent(
      "auto-boost",
      `Auto tick: xử lý ${processed} file qua ${roundResults.length} lô`,
    ).catch(() => null);
    result.process = { processed, rounds: roundResults };

    const dueSync =
      state.tickCount === 0 || state.tickCount % state.config.syncEveryTicks === 0;
    if (dueSync) {
      const sync = await runItySyncRound({
        daysBack: 7,
        maxPages: 1,
        pageLimit: 100,
        autoAnalyze: false,
      }).catch((error) => ({
        id: "",
        discovered: 0,
        imported: 0,
        skipped: 0,
        failed: 0,
        pendingBrowserDownload: 0,
        remoteTotal: 0,
        lastPage: 1,
        hasMore: false,
        errors: [error instanceof Error ? error.message : "sync failed"],
      }));
      state.stats.syncRuns += 1;
      result.sync = sync;
    }

    const dueImport =
      state.tickCount === 0 ||
      state.tickCount % state.config.importEveryTicks === 0;
    if (dueImport) {
      const imported = await syncRecordingsFromChotKiem({
        limit: state.config.importLimit,
        downloadAudio: true,
        newOnly: true,
      });
      state.stats.importRuns += 1;
      state.stats.imported += imported.imported + imported.updated;
      await noteDownloadEvent(
        "auto-import",
        `Auto import: +${imported.imported} mới, ${imported.updated} cập nhật, audio ${imported.withAudio}`,
      ).catch(() => null);
      result.import = imported;
    }

    const dueReanalyze =
      state.tickCount > 0 &&
      state.tickCount % state.config.reanalyzeEveryTicks === 0;
    // Prefer draining unanalyzed / outdated analyses first.
    const pending = await analyzePendingRecordings({ limit: 40 });
    if (pending.updated > 0) {
      state.stats.reanalyzeRuns += 1;
      state.stats.reanalyzed += pending.updated;
      result.reanalyze = pending;
    } else if (dueReanalyze) {
      const reanalyzed = await reanalyzeRecordings({
        limit: 80,
        force: true,
        pendingOnly: false,
      });
      state.stats.reanalyzeRuns += 1;
      state.stats.reanalyzed += reanalyzed.updated;
      result.reanalyze = reanalyzed;
    }

    const [status, progress, library] = await Promise.all([
      fetchItyDownloadStatus().catch(() => null),
      sampleDownloadProgress(),
      getLibraryStats(),
    ]);

    result.snapshot = {
      pending: status?.pendingCount ?? progress.pendingCount,
      libraryTotal: library.total,
      libraryWithAudio: library.withAudio,
      readyForRecreation: library.readyForRecreation,
      filesPerMinute: progress.filesPerMinute,
      lifetimeFilesPerMinute: progress.lifetimeFilesPerMinute,
    };

    state.tickCount += 1;
    state.lastTickAt = new Date().toISOString();
    state.lastError = null;
    state.consecutiveErrors = 0;
    const message = `Tick #${state.tickCount}: proxy ${proxySnap.livingCount}, process ${processed}, thư viện ${library.withAudio}/${library.total} (ready ${library.readyForRecreation}), tốc độ ${progress.filesPerMinute.toFixed(1)} file/phút (${Date.now() - tickStarted}ms)`;
    state.lastSummary = message;
    result.message = message;
    await saveState(state);

    return { state, skipped: false, result };
  } catch (error) {
    state.tickCount += 1;
    state.lastTickAt = new Date().toISOString();
    state.consecutiveErrors += 1;
    state.lastError =
      error instanceof Error ? error.message : "auto tick failed";
    state.lastSummary = `Tick lỗi #${state.tickCount}: ${state.lastError}`;
    result.message = state.lastSummary;
    result.error = state.lastError;
    await saveState(state);
    return { state, skipped: false, result };
  }
}
