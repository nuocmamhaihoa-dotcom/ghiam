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
  let pendingAnalysis = 0;
  for (const item of items) {
    if (!item.hasTranscript && !item.callSummary) continue;
    const version = item.analysisVersion ?? 0;
    if (version < ANALYSIS_VERSION) pendingAnalysis += 1;
  }
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
  };
}

/** Import calls from ChốtKiểm into local durable library. */
export async function syncRecordingsFromChotKiem(options?: {
  limit?: number;
  downloadAudio?: boolean;
}): Promise<{
  imported: number;
  updated: number;
  skipped: number;
  withAudio: number;
  errors: string[];
  totalRemote: number;
}> {
  const limit = Math.max(1, Math.min(options?.limit ?? 30, 100));
  const downloadAudio = options?.downloadAudio !== false;
  await ensureDirs();

  const report = await ckFetch<{
    rows?: Array<{ id: string; hasAudio?: boolean; audioUrl?: string | null }>;
    totalCalls?: number;
  }>(" /api/calls/criteria-report?limit=80".trim());

  const ids: string[] = [];
  for (const row of report.rows || []) {
    if (row.hasAudio || row.audioUrl) ids.push(row.id);
    if (ids.length >= limit) break;
  }
  if (ids.length < Math.min(10, limit)) {
    const listed = await ckFetch<{ calls?: Array<{ id: string }> }>(
      `/api/calls?limit=${limit}`,
    );
    for (const c of listed.calls || []) {
      if (!ids.includes(c.id)) ids.push(c.id);
      if (ids.length >= limit) break;
    }
  }

  const index = await readIndex();
  const byId = new Map(index.items.map((i) => [i.id, i] as const));
  let imported = 0;
  let updated = 0;
  let skipped = 0;
  let withAudio = 0;
  const errors: string[] = [];
  const queue = ids.slice(0, limit);
  const workers = Math.max(1, Math.min(3, queue.length));

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

    if (downloadAudio && (call.audioUrl || call.hasAudio)) {
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

  let cursor = 0;
  async function worker() {
    while (cursor < queue.length) {
      const id = queue[cursor]!;
      cursor += 1;
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

  return {
    imported,
    updated,
    skipped,
    withAudio,
    errors: errors.slice(0, 20),
    totalRemote: num(report.totalCalls),
  };
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
