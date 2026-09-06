/**
 * Local recording library: import ChốtKiểm calls (audio + transcript),
 * persist under /data/recordings, run deep sales analysis for recreation.
 */

import { createWriteStream, promises as fs } from "fs";
import path from "path";
import { Readable } from "stream";
import { pipeline } from "stream/promises";
import { ckFetch, maskPhone } from "./chotKiemClient";
import {
  ANALYSIS_VERSION,
  analyzeCallDeep,
  type DeepCallAnalysis,
} from "./deepCallAnalysis";

export type RecordingMeta = {
  id: string;
  externalId: string;
  title: string;
  agentName: string;
  phoneMasked: string;
  fileName: string | null;
  createdAt: number;
  importedAt: string;
  updatedAt: string;
  hasAudio: boolean;
  audioFile: string | null;
  audioBytes: number;
  audioMime: string | null;
  hasTranscript: boolean;
  transcriptChars: number;
  durationSec: number;
  grade: string | null;
  overallScore: number | null;
  isComplete: boolean | null;
  callSummary: string | null;
  outcome: "won" | "lost" | "callback" | "unknown";
  readinessScore: number;
  analysisVersion: number;
  sourceAudioUrl: string | null;
};


function normalizeMeta(raw: RecordingMeta): RecordingMeta {
  return {
    ...raw,
    analysisVersion: raw.analysisVersion ?? 0,
    readinessScore: raw.readinessScore ?? 0,
  };
}

export type RecordingRecord = {
  meta: RecordingMeta;
  transcript: string;
  analysis: DeepCallAnalysis;
  scorecard: Record<string, unknown> | null;
};

type IndexFile = {
  updatedAt: string;
  items: RecordingMeta[];
};

type RemoteCall = {
  id: string;
  fileName?: string;
  employeeName?: string;
  phoneNumber?: string;
  createdAt?: number;
  overallScore?: number;
  weightedScore?: number;
  grade?: string;
  isComplete?: boolean;
  callSummary?: string;
  transcript?: string;
  audioMime?: string | null;
  hasAudio?: boolean;
  audioUrl?: string | null;
  externalId?: string;
  scorecard?: {
    criteria?: Array<{
      key: string;
      label?: string;
      passed?: boolean;
      required?: boolean;
      value?: string | null;
      evidence?: string | null;
    }>;
    [key: string]: unknown;
  };
};

const ROOT = path.join(process.cwd(), "data", "recordings");
const INDEX_PATH = path.join(ROOT, "index.json");
const SYNC_CURSOR_PATH = path.join(ROOT, "sync-cursor.json");

type SyncCursor = {
  /** @deprecated kept for backward compatibility */
  criteriaOffset: number;
  /** Offset into /api/calls?analysisStatus=analyzed */
  analyzedOffset: number;
  /** Offset into /api/calls?hasAudio=true (downloaded, maybe chưa STT) */
  audioOffset: number;
  remoteAnalyzedTotal: number;
  remoteWithAudioTotal: number;
  updatedAt: string;
};

async function readSyncCursor(): Promise<SyncCursor> {
  try {
    const raw = await fs.readFile(SYNC_CURSOR_PATH, "utf8");
    const parsed = JSON.parse(raw) as Partial<SyncCursor>;
    const criteriaOffset = Math.max(0, Number(parsed.criteriaOffset) || 0);
    return {
      criteriaOffset,
      analyzedOffset: Math.max(
        0,
        Number(parsed.analyzedOffset ?? criteriaOffset) || 0,
      ),
      audioOffset: Math.max(0, Number(parsed.audioOffset) || 0),
      remoteAnalyzedTotal: Math.max(0, Number(parsed.remoteAnalyzedTotal) || 0),
      remoteWithAudioTotal: Math.max(0, Number(parsed.remoteWithAudioTotal) || 0),
      updatedAt: parsed.updatedAt || new Date(0).toISOString(),
    };
  } catch {
    return {
      criteriaOffset: 0,
      analyzedOffset: 0,
      audioOffset: 0,
      remoteAnalyzedTotal: 0,
      remoteWithAudioTotal: 0,
      updatedAt: new Date(0).toISOString(),
    };
  }
}

async function writeSyncCursor(cursor: SyncCursor) {
  await ensureDirs();
  const payload: SyncCursor = {
    criteriaOffset: Math.max(0, cursor.criteriaOffset || 0),
    analyzedOffset: Math.max(0, cursor.analyzedOffset || 0),
    audioOffset: Math.max(0, cursor.audioOffset || 0),
    remoteAnalyzedTotal: Math.max(0, cursor.remoteAnalyzedTotal || 0),
    remoteWithAudioTotal: Math.max(0, cursor.remoteWithAudioTotal || 0),
    updatedAt: new Date().toISOString(),
  };
  await fs.writeFile(SYNC_CURSOR_PATH, JSON.stringify(payload, null, 2), "utf8");
}


function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function inferOutcome(call: RemoteCall): RecordingMeta["outcome"] {
  const summary = `${call.callSummary || ""} ${call.transcript || ""}`.toLowerCase();
  if (call.isComplete || /đồng ý nhận hàng|chốt đơn|đã chốt/.test(summary)) {
    return "won";
  }
  if (/từ chối|không nhận|không mua|mất đơn/.test(summary)) return "lost";
  if (/gọi lại|callback|mai nói/.test(summary)) return "callback";
  if ((call.grade || "").toUpperCase() === "A" && (call.overallScore || 0) >= 80) {
    return "won";
  }
  return "unknown";
}

function estimateDurationSec(transcript: string): number {
  const words = Math.max(
    transcript.split(/\s+/).filter(Boolean).length,
    Math.round(transcript.replace(/\s+/g, "").length / 4),
  );
  return Math.max(25, Math.min(900, Math.round((words / 140) * 60) || 45));
}

async function ensureDirs() {
  await fs.mkdir(ROOT, { recursive: true });
}

async function readIndex(): Promise<IndexFile> {
  try {
    const raw = await fs.readFile(INDEX_PATH, "utf8");
    return JSON.parse(raw) as IndexFile;
  } catch {
    return { updatedAt: new Date(0).toISOString(), items: [] };
  }
}

async function writeIndex(index: IndexFile) {
  await ensureDirs();
  index.updatedAt = new Date().toISOString();
  await fs.writeFile(INDEX_PATH, JSON.stringify(index, null, 2), "utf8");
}

const SYNC_LOCK_PATH = path.join(ROOT, ".sync.lock");

async function acquireSyncLock(timeoutMs = 120_000): Promise<() => Promise<void>> {
  const started = Date.now();
  while (true) {
    try {
      const handle = await fs.open(SYNC_LOCK_PATH, "wx");
      await handle.writeFile(
        JSON.stringify({ pid: process.pid, at: new Date().toISOString() }),
        "utf8",
      );
      await handle.close();
      return async () => {
        await fs.unlink(SYNC_LOCK_PATH).catch(() => null);
      };
    } catch {
      try {
        const raw = await fs.readFile(SYNC_LOCK_PATH, "utf8");
        const parsed = JSON.parse(raw) as { at?: string };
        const age = Date.now() - new Date(parsed.at || 0).getTime();
        if (age > timeoutMs) {
          await fs.unlink(SYNC_LOCK_PATH).catch(() => null);
          continue;
        }
      } catch {
        await fs.unlink(SYNC_LOCK_PATH).catch(() => null);
        continue;
      }
      if (Date.now() - started > timeoutMs) {
        throw new Error("Sync lock timeout — thử lại sau");
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }
}

/** Rebuild index.json from meta.json files on disk (recovery after races). */
export async function rebuildIndexFromDisk(): Promise<{
  indexed: number;
  dirs: number;
}> {
  await ensureDirs();
  const entries = await fs.readdir(ROOT, { withFileTypes: true });
  const items: RecordingMeta[] = [];
  let dirs = 0;
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    dirs += 1;
    try {
      const raw = await fs.readFile(
        path.join(ROOT, entry.name, "meta.json"),
        "utf8",
      );
      const meta = normalizeMeta(JSON.parse(raw) as RecordingMeta);
      if (meta.id) items.push(meta);
    } catch {
      // skip broken folders
    }
  }
  items.sort((a, b) => b.createdAt - a.createdAt);
  await writeIndex({ updatedAt: new Date().toISOString(), items });
  return { indexed: items.length, dirs };
}

async function ensureIndexHealthy(): Promise<IndexFile> {
  let index = await readIndex();
  try {
    const entries = await fs.readdir(ROOT, { withFileTypes: true });
    const dirCount = entries.filter((e) => e.isDirectory()).length;
    if (dirCount > index.items.length + 25) {
      const rebuilt = await rebuildIndexFromDisk();
      index = await readIndex();
      console.warn(
        `[recordings] rebuilt index ${rebuilt.indexed}/${rebuilt.dirs} (was ${index.items.length} before repair pass)`,
      );
    }
  } catch {
    // ignore health check errors
  }
  return index;
}


function recordDir(id: string) {
  return path.join(ROOT, id);
}

function baseUrl() {
  return (process.env.CHOTKIEM_BASE_URL || "http://222.255.215.55").replace(
    /\/$/,
    "",
  );
}

function basicAuth() {
  const user = process.env.CHOTKIEM_USER || "";
  const password = process.env.CHOTKIEM_PASSWORD || "";
  return "Basic " + Buffer.from(`${user}:${password}`).toString("base64");
}

async function downloadAudioToDisk(
  audioUrl: string,
  destPath: string,
): Promise<{ bytes: number; mime: string | null }> {
  const url = audioUrl.startsWith("http")
    ? audioUrl
    : `${baseUrl()}${audioUrl}`;
  const res = await fetch(url, {
    headers: {
      Authorization: basicAuth(),
      "X-Hub-User": process.env.CHOTKIEM_USER || "",
      "X-Hub-Pass": process.env.CHOTKIEM_PASSWORD || "",
      Accept: "*/*",
    },
    cache: "no-store",
  });
  if (!res.ok || !res.body) {
    throw new Error(`Audio download failed (${res.status})`);
  }
  const mime = res.headers.get("content-type");
  const nodeStream = Readable.fromWeb(
    res.body as import("stream/web").ReadableStream,
  );
  await pipeline(nodeStream, createWriteStream(destPath));
  const stat = await fs.stat(destPath);
  return { bytes: stat.size, mime };
}

export async function listRecordings(): Promise<RecordingMeta[]> {
  const index = await readIndex();
  return [...index.items]
    .map(normalizeMeta)
    .sort((a, b) => b.createdAt - a.createdAt);
}

export async function getRecording(id: string): Promise<RecordingRecord | null> {
  try {
    const dir = recordDir(id);
    const [metaRaw, transcript, analysisRaw, scorecardRaw] = await Promise.all([
      fs.readFile(path.join(dir, "meta.json"), "utf8"),
      fs.readFile(path.join(dir, "transcript.txt"), "utf8").catch(() => ""),
      fs.readFile(path.join(dir, "analysis.json"), "utf8"),
      fs.readFile(path.join(dir, "scorecard.json"), "utf8").catch(() => null),
    ]);
    return {
      meta: JSON.parse(metaRaw) as RecordingMeta,
      transcript,
      analysis: JSON.parse(analysisRaw) as DeepCallAnalysis,
      scorecard: scorecardRaw
        ? (JSON.parse(scorecardRaw) as Record<string, unknown>)
        : null,
    };
  } catch {
    return null;
  }
}

export async function getRecordingAudioPath(
  id: string,
): Promise<{ filePath: string; mime: string | null } | null> {
  const rec = await getRecording(id);
  if (!rec?.meta.audioFile) return null;
  const filePath = path.join(recordDir(id), rec.meta.audioFile);
  try {
    await fs.access(filePath);
    return { filePath, mime: rec.meta.audioMime };
  } catch {
    return null;
  }
}

export async function getLibraryStats() {
  const items = await listRecordings();
  const cursor = await readSyncCursor();
  let pendingAnalysis = 0;
  for (const item of items) {
    if (!item.hasTranscript && !item.callSummary) continue;
    const version = item.analysisVersion ?? 0;
    if (version < ANALYSIS_VERSION) pendingAnalysis += 1;
  }
  const remoteAnalyzedTotal = cursor.remoteAnalyzedTotal;
  const remoteWithAudioTotal = cursor.remoteWithAudioTotal;
  return {
    total: items.length,
    withAudio: items.filter((i) => i.hasAudio).length,
    withTranscript: items.filter((i) => i.hasTranscript).length,
    readyForRecreation: items.filter((i) => i.readinessScore >= 70).length,
    pendingAnalysis,
    analysisVersion: ANALYSIS_VERSION,
    avgReadiness:
      items.length === 0
        ? 0
        : Math.round(
            items.reduce((s, i) => s + i.readinessScore, 0) / items.length,
          ),
    won: items.filter((i) => i.outcome === "won").length,
    remoteAnalyzedTotal,
    remoteWithAudioTotal,
    syncCoveragePct:
      remoteAnalyzedTotal > 0
        ? Math.min(100, Math.round((items.length / remoteAnalyzedTotal) * 100))
        : 0,
  };
}

/** Import calls from ChốtKiểm into local durable library. */
export async function syncRecordingsFromChotKiem(options?: {
  limit?: number;
  downloadAudio?: boolean;
  /** When true (default), prefer calls not yet in the local library. */
  newOnly?: boolean;
}): Promise<{
  imported: number;
  updated: number;
  skipped: number;
  withAudio: number;
  errors: string[];
  totalRemote: number;
  nextOffset: number;
  scannedRemote: number;
  remoteAnalyzedTotal: number;
  remoteWithAudioTotal: number;
  localTotal: number;
}> {
  const limit = Math.max(1, Math.min(options?.limit ?? 80, 200));
  const downloadAudio = options?.downloadAudio !== false;
  const newOnly = options?.newOnly !== false;
  await ensureDirs();
  const releaseLock = await acquireSyncLock();

  try {
  const index = await ensureIndexHealthy();
  const byId = new Map(index.items.map((i) => [i.id, i] as const));
  const known = new Set(byId.keys());

  const syncCursor = await readSyncCursor();
  let analyzedOffset = syncCursor.analyzedOffset;
  let audioOffset = syncCursor.audioOffset;
  let totalRemote = Math.max(
    syncCursor.remoteAnalyzedTotal,
    syncCursor.remoteWithAudioTotal,
  );
  let remoteAnalyzedTotal = syncCursor.remoteAnalyzedTotal;
  let remoteWithAudioTotal = syncCursor.remoteWithAudioTotal;
  let scannedRemote = 0;
  const pageSize = 100;
  const maxPages = 15;
  const newIds: string[] = [];
  const oldIds: string[] = [];
  const seen = new Set<string>();

  async function collectFromCalls(pathBase: string, startOffset: number) {
    let offset = startOffset;
    let wrapped = false;
    for (let page = 0; page < maxPages; page += 1) {
      if (newIds.length >= limit) break;
      const listed = await ckFetch<{
        calls?: Array<{
          id: string;
          hasAudio?: boolean;
          audioUrl?: string | null;
          analysisStatus?: string;
        }>;
        total?: number;
        hasMore?: boolean;
      }>(`${pathBase}&offset=${offset}`);

      if (typeof listed.total === "number") {
        totalRemote = Math.max(totalRemote, listed.total);
        if (pathBase.includes("analysisStatus=analyzed")) {
          remoteAnalyzedTotal = Math.max(remoteAnalyzedTotal, listed.total);
        }
        if (pathBase.includes("hasAudio=true")) {
          remoteWithAudioTotal = Math.max(remoteWithAudioTotal, listed.total);
        }
      }

      const calls = listed.calls || [];
      scannedRemote += calls.length;
      for (const call of calls) {
        if (!call?.id || seen.has(call.id)) continue;
        if (!(call.hasAudio || call.audioUrl)) continue;
        seen.add(call.id);
        if (known.has(call.id)) oldIds.push(call.id);
        else newIds.push(call.id);
      }

      const next = offset + calls.length;
      const hasMore =
        (listed.hasMore ?? calls.length >= pageSize) && calls.length > 0;

      if (newIds.length >= limit) {
        offset = next;
        break;
      }
      if (!hasMore) {
        if (!wrapped) {
          wrapped = true;
          offset = 0;
          continue;
        }
        offset = 0;
        break;
      }
      offset = next;
    }
    return offset;
  }

  // 1) Drain analyzed+downloaded calls first (have transcript for deep analysis).
  analyzedOffset = await collectFromCalls(
    `/api/calls?analysisStatus=analyzed&limit=${pageSize}`,
    analyzedOffset,
  );

  // 2) Also pull any downloaded audio not yet analyzed on ChốtKiểm.
  if (newIds.length < limit) {
    audioOffset = await collectFromCalls(
      `/api/calls?hasAudio=true&limit=${pageSize}`,
      audioOffset,
    );
  }

  const ids: string[] = newIds.slice(0, limit);
  if (!newOnly || ids.length === 0) {
    for (const id of oldIds) {
      if (ids.length >= limit) break;
      if (!ids.includes(id)) ids.push(id);
    }
  }

  let imported = 0;
  let updated = 0;
  let skipped = 0;
  let withAudio = 0;
  const errors: string[] = [];
  const queue = ids.slice(0, limit);
  const workers = Math.max(1, Math.min(5, queue.length));

  async function importOne(id: string) {
    const call = await ckFetch<RemoteCall>(`/api/calls/${id}`);
    const transcript = (call.transcript || "").trim();
    if (!transcript && !call.hasAudio && !call.audioUrl) {
      return { kind: "skipped" as const };
    }

    const dir = recordDir(id);
    await fs.mkdir(dir, { recursive: true });

    const durationSec = estimateDurationSec(
      transcript || call.callSummary || "",
    );
    const outcome = inferOutcome(call);
    const analysis = analyzeCallDeep({
      transcript: transcript || call.callSummary || "",
      durationSec,
      outcome,
      callSummary: call.callSummary,
      grade: call.grade,
      overallScore: call.overallScore ?? call.weightedScore,
      isComplete: call.isComplete,
      scorecard: {
        grade: call.grade,
        overallScore: call.overallScore ?? call.weightedScore,
        isComplete: call.isComplete,
        criteria: (call.scorecard?.criteria || []).map((c) => ({
          key: c.key,
          label: c.label,
          passed: Boolean(c.passed),
          required: c.required,
          value: c.value ?? null,
          evidence: c.evidence ?? null,
        })),
      },
    });

    let audioFile = byId.get(id)?.audioFile ?? null;
    let audioBytes = byId.get(id)?.audioBytes ?? 0;
    let audioMime = byId.get(id)?.audioMime ?? call.audioMime ?? null;
    let gotAudio = false;

    const audioAlreadyOnDisk =
      Boolean(audioFile) &&
      (await fs
        .access(path.join(dir, audioFile!))
        .then(() => true)
        .catch(() => false));

    if (
      downloadAudio &&
      !audioAlreadyOnDisk &&
      (call.audioUrl || call.hasAudio)
    ) {
      const audioUrl = call.audioUrl || `/api/calls/${id}/audio`;
      const ext = (call.fileName || "").toLowerCase().endsWith(".mp3")
        ? "mp3"
        : "wav";
      const destName = `audio.${ext}`;
      try {
        const dl = await downloadAudioToDisk(
          audioUrl,
          path.join(dir, destName),
        );
        audioFile = destName;
        audioBytes = dl.bytes;
        audioMime =
          dl.mime ||
          audioMime ||
          (ext === "mp3" ? "audio/mpeg" : "audio/wav");
        gotAudio = true;
      } catch (error) {
        errors.push(
          `${id}: audio ${error instanceof Error ? error.message : "fail"}`,
        );
      }
    } else if (audioAlreadyOnDisk) {
      gotAudio = true;
    }

    if (transcript) {
      await fs.writeFile(path.join(dir, "transcript.txt"), transcript, "utf8");
    }
    await fs.writeFile(
      path.join(dir, "analysis.json"),
      JSON.stringify(analysis, null, 2),
      "utf8",
    );
    if (call.scorecard) {
      await fs.writeFile(
        path.join(dir, "scorecard.json"),
        JSON.stringify(call.scorecard, null, 2),
        "utf8",
      );
    }

    const now = new Date().toISOString();
    const existing = byId.get(id);
    const meta: RecordingMeta = {
      id,
      externalId: call.externalId || id,
      title:
        call.callSummary?.split("·")[0]?.trim() ||
        call.fileName ||
        `Cuộc gọi ${maskPhone(call.phoneNumber)}`,
      agentName: call.employeeName || "ITY",
      phoneMasked: maskPhone(call.phoneNumber),
      fileName: call.fileName || null,
      createdAt: num(call.createdAt, Date.now()),
      importedAt: existing?.importedAt || now,
      updatedAt: now,
      hasAudio: Boolean(audioFile),
      audioFile,
      audioBytes,
      audioMime,
      hasTranscript: Boolean(transcript),
      transcriptChars: transcript.length,
      durationSec,
      grade: call.grade || null,
      overallScore: call.overallScore ?? call.weightedScore ?? null,
      isComplete: call.isComplete ?? null,
      callSummary: call.callSummary || null,
      outcome,
      readinessScore: analysis.readinessScore,
      analysisVersion: ANALYSIS_VERSION,
      sourceAudioUrl: call.audioUrl || null,
    };
    await fs.writeFile(
      path.join(dir, "meta.json"),
      JSON.stringify(meta, null, 2),
      "utf8",
    );

    byId.set(id, meta);
    return {
      kind: existing ? ("updated" as const) : ("imported" as const),
      gotAudio,
    };
  }

  let queueCursor = 0;
  async function worker() {
    while (queueCursor < queue.length) {
      const id = queue[queueCursor]!;
      queueCursor += 1;
      try {
        const result = await importOne(id);
        if (result.kind === "skipped") skipped += 1;
        else if (result.kind === "updated") updated += 1;
        else imported += 1;
        if (result.kind !== "skipped" && result.gotAudio) withAudio += 1;
      } catch (error) {
        errors.push(`${id}: ${error instanceof Error ? error.message : "error"}`);
      }
    }
  }

  await Promise.all(Array.from({ length: workers }, () => worker()));

  index.items = [...byId.values()].sort((a, b) => b.createdAt - a.createdAt);
  await writeIndex(index);
  await writeSyncCursor({
    criteriaOffset: analyzedOffset,
    analyzedOffset,
    audioOffset,
    remoteAnalyzedTotal,
    remoteWithAudioTotal,
    updatedAt: new Date().toISOString(),
  });

  return {
    imported,
    updated,
    skipped,
    withAudio,
    errors: errors.slice(0, 20),
    totalRemote: Math.max(totalRemote, remoteAnalyzedTotal, remoteWithAudioTotal),
    nextOffset: analyzedOffset,
    scannedRemote,
    remoteAnalyzedTotal,
    remoteWithAudioTotal,
    localTotal: index.items.length,
  };
  } finally {
    await releaseLock();
  }
}

/** Re-run deep analysis on imported recordings. Default: only pending/outdated. */
export async function reanalyzeRecordings(options?: {
  limit?: number;
  /** Analyze only missing/outdated analyses (default true). */
  pendingOnly?: boolean;
  /** Force re-analyze even if already on latest version. */
  force?: boolean;
}): Promise<{
  updated: number;
  scanned: number;
  pendingLeft: number;
  readyForRecreation: number;
  avgReadiness: number;
  analysisVersion: number;
}> {
  const limit = Math.max(1, Math.min(options?.limit ?? 100, 300));
  const pendingOnly = options?.force ? false : options?.pendingOnly !== false;
  const index = await readIndex();
  let updated = 0;
  let scanned = 0;

  for (const item of index.items) {
    if (updated >= limit) break;
    scanned += 1;
    const dir = recordDir(item.id);
    const transcript = await fs
      .readFile(path.join(dir, "transcript.txt"), "utf8")
      .catch(() => "");
    if (!transcript.trim() && !item.callSummary) continue;

    const currentVersion = item.analysisVersion ?? 0;
    const analysisPath = path.join(dir, "analysis.json");
    const hasAnalysis = await fs
      .access(analysisPath)
      .then(() => true)
      .catch(() => false);

    if (pendingOnly && hasAnalysis && currentVersion >= ANALYSIS_VERSION) {
      continue;
    }

    let scorecard: DeepCallAnalysis["chotKiem"] | undefined;
    try {
      const raw = await fs.readFile(path.join(dir, "scorecard.json"), "utf8");
      scorecard = JSON.parse(raw) as DeepCallAnalysis["chotKiem"];
    } catch {
      scorecard = undefined;
    }

    const analysis = analyzeCallDeep({
      transcript: transcript || item.callSummary || "",
      durationSec: item.durationSec,
      outcome: item.outcome,
      callSummary: item.callSummary || undefined,
      grade: item.grade || undefined,
      overallScore: item.overallScore ?? undefined,
      isComplete: item.isComplete ?? undefined,
      scorecard,
    });

    await fs.writeFile(
      analysisPath,
      JSON.stringify(analysis, null, 2),
      "utf8",
    );

    item.readinessScore = analysis.readinessScore;
    item.analysisVersion = ANALYSIS_VERSION;
    item.updatedAt = new Date().toISOString();
    await fs.writeFile(
      path.join(dir, "meta.json"),
      JSON.stringify(item, null, 2),
      "utf8",
    );
    updated += 1;
  }

  await writeIndex(index);
  const stats = await getLibraryStats();
  return {
    updated,
    scanned,
    pendingLeft: stats.pendingAnalysis,
    readyForRecreation: stats.readyForRecreation,
    avgReadiness: stats.avgReadiness,
    analysisVersion: ANALYSIS_VERSION,
  };
}

/** Alias: analyze all unanalyzed / outdated recordings in the library. */
export async function analyzePendingRecordings(options?: { limit?: number }) {
  return reanalyzeRecordings({
    limit: options?.limit ?? 100,
    pendingOnly: true,
  });
}
