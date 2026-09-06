import { NextRequest, NextResponse } from "next/server";
import {
  noteDownloadEvent,
  sampleDownloadProgress,
} from "@/lib/ityDownloadProgress";
import { processItyPendingDownloads, fetchItyPendingDownloads, startItyProxyHunt, fetchItyDownloadStatus } from "@/lib/itySyncClient";
import { ensureProxyPool } from "@/lib/proxyPool";
import { syncRecordingsFromChotKiem } from "@/lib/recordingLibrary";
import { downloadThenAnalyzeCalls } from "@/lib/ityImmediateAnalyze";

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
          const immediate = await downloadThenAnalyzeCalls({
            callIds,
            concurrency,
            timeoutMs: 45_000,
            autoAnalyze: true,
          });
          results.push({
            round: i + 1,
            ok: !immediate.downloadError,
            processed: immediate.download,
            analyzed: immediate.analyze,
            batch: callIds.length,
            timedOut: immediate.timedOut,
            error: immediate.downloadError,
          });
          if (immediate.timedOut) break;
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
      // Safety net: import any newly completed remote calls and deep-analyze.
      const safetyImport = await syncRecordingsFromChotKiem({
        limit: Math.max(batchSize * 2, 20),
        downloadAudio: true,
        newOnly: false,
      }).catch((error) => ({
        imported: 0,
        updated: 0,
        skipped: 0,
        withAudio: 0,
        analyzed: 0,
        errors: [error instanceof Error ? error.message : "import failed"],
        localTotal: 0,
      }));
      const progress = await noteDownloadEvent(
        "boosting",
        `Boost ${results.length} lô · phân tích ngay · pending ~${download?.pendingCount ?? "?"} · kho +${(safetyImport as { imported?: number }).imported ?? 0}/${(safetyImport as { analyzed?: number }).analyzed ?? 0}`,
      );

      return NextResponse.json({
        ok: true,
        action,
        rounds: results.length,
        results,
        pendingCount: download?.pendingCount ?? null,
        analyze: safetyImport,
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
        `Đã lưu+phân tích: +${result.imported} mới, ${result.updated} cập nhật, audio ${result.withAudio}, sâu ${result.analyzed ?? 0}`,
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
