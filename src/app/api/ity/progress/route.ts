import { NextRequest, NextResponse } from "next/server";
import {
  noteDownloadEvent,
  sampleDownloadProgress,
} from "@/lib/ityDownloadProgress";
import { processItyPendingDownloads, fetchItyPendingDownloads, startItyProxyHunt, fetchItyDownloadStatus } from "@/lib/itySyncClient";
import { ensureProxyPool } from "@/lib/proxyPool";
import { syncRecordingsFromChotKiem } from "@/lib/recordingLibrary";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

/** GET: download speed + work progress board. */
export async function GET() {
  try {
    const progress = await sampleDownloadProgress();
    return NextResponse.json({ ok: true, progress });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}

type ProgressAction = {
  action?: "sample" | "boost-downloads" | "import-library";
  rounds?: number;
  batchSize?: number;
  concurrency?: number;
  importLimit?: number;
};

/**
 * POST actions for the progress board:
 * - sample: refresh speed metrics
 * - boost-downloads: kick hunt + several process-pending rounds
 * - import-library: permanently save + analyze completed ChốtKiểm calls
 */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as ProgressAction;
    const action = body.action || "sample";

    if (action === "sample") {
      const progress = await sampleDownloadProgress({
        phase: "sampling",
        message: "Làm mới bảng tốc độ",
        event: "Làm mới bảng tốc độ",
      });
      return NextResponse.json({ ok: true, action, progress });
    }

    if (action === "boost-downloads") {
      await ensureProxyPool({ minLiving: 20 }).catch(() => null);
      await startItyProxyHunt().catch(() => null);

      const rounds = Math.max(1, Math.min(body.rounds ?? 4, 8));
      const batchSize = Math.max(1, Math.min(body.batchSize ?? 8, 8));
      const concurrency = Math.max(1, Math.min(body.concurrency ?? 3, 4));
      const results: Array<Record<string, unknown>> = [];

      for (let i = 0; i < rounds; i += 1) {
        const pending = await fetchItyPendingDownloads(batchSize).catch(() => ({
          count: 0,
          items: [] as Array<{ callId: string }>,
        }));
        const callIds = pending.items.map((item) => item.callId);
        if (!callIds.length) break;
        try {
          const processed = await processItyPendingDownloads({
            callIds,
            concurrency,
            timeoutMs: 45_000,
            autoAnalyze: false,
          });
          results.push({ round: i + 1, ok: true, processed, batch: callIds.length });
        } catch (error) {
          results.push({
            round: i + 1,
            ok: false,
            timedOut: true,
            error: error instanceof Error ? error.message : "error",
            batch: callIds.length,
          });
        }
      }

      const download = await fetchItyDownloadStatus().catch(() => null);
      const progress = await noteDownloadEvent(
        "boosting",
        `Boost ${results.length} lô (batch≤${batchSize}, concurrency=${concurrency}) · pending ~${download?.pendingCount ?? "?"}`,
      );

      return NextResponse.json({
        ok: true,
        action,
        rounds: results.length,
        results,
        pendingCount: download?.pendingCount ?? null,
        progress,
      });
    }

    if (action === "import-library") {
      const result = await syncRecordingsFromChotKiem({
        limit: Math.max(1, Math.min(body.importLimit ?? 30, 80)),
        downloadAudio: true,
      });
      const progress = await noteDownloadEvent(
        "importing",
        `Đã lưu thư viện: +${result.imported} mới, ${result.updated} cập nhật, audio ${result.withAudio}`,
      );
      return NextResponse.json({ ok: true, action, result, progress });
    }

    return NextResponse.json(
      { ok: false, error: `Unknown action: ${action}` },
      { status: 400 },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}
