import { NextRequest, NextResponse } from "next/server";
import {
  fetchItyDownloadStatus,
  fetchItyPendingDownloads,
  fetchItySettingsSummary,
  processItyPendingDownloads,
  runItySyncRound,
  startItyProxyHunt,
} from "@/lib/itySyncClient";
import { downloadThenAnalyzeCalls } from "@/lib/ityImmediateAnalyze";
import {
  noteDownloadEvent,
  sampleDownloadProgress,
} from "@/lib/ityDownloadProgress";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

function maskPhone(phone?: string): string {
  if (!phone) return "***";
  const digits = phone.replace(/\D/g, "");
  if (digits.length < 6) return "***";
  return `${digits.slice(0, 3)}***${digits.slice(-3)}`;
}

/** ITY status: accounts + pending recording downloads. */
export async function GET() {
  try {
    const [settings, download, pending, progress] = await Promise.all([
      fetchItySettingsSummary(),
      fetchItyDownloadStatus(),
      fetchItyPendingDownloads(5),
      sampleDownloadProgress().catch(() => null),
    ]);

    return NextResponse.json({
      ok: true,
      settings,
      download,
      pending: {
        count: pending.count,
        sample: pending.items.map((i) => ({
          callId: i.callId,
          phoneMasked: maskPhone(i.phoneNumber),
          fileName: i.fileName,
          createdAt: i.createdAt,
        })),
      },
      progress,
      fetchedAt: new Date().toISOString(),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}

type ItyActionBody = {
  action?: "sync-round" | "start-downloads" | "process-pending";
  daysBack?: number;
  startPage?: number;
  maxPages?: number;
  pageLimit?: number;
  batchSize?: number;
  concurrency?: number;
  autoAnalyze?: boolean;
  callIds?: string[];
};

/**
 * Client-driven ITY sync (avoids single long request timeouts):
 * - sync-round: one metadata page batch (default daysBack=10)
 * - start-downloads: kick proxy-hunt/drain only (no blocking process-pending)
 * - process-pending: download audio for next / given callIds (small batches)
 */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as ItyActionBody;
    const action = body.action || "sync-round";

    if (action === "sync-round") {
      const round = await runItySyncRound({
        daysBack: body.daysBack ?? 10,
        startPage: body.startPage ?? 1,
        maxPages: body.maxPages ?? 1,
        pageLimit: body.pageLimit ?? 100,
        autoAnalyze: body.autoAnalyze ?? false,
      });

      return NextResponse.json({
        ok: true,
        action,
        round,
        nextStartPage: round.hasMore ? round.lastPage + 1 : null,
      });
    }

    if (action === "start-downloads") {
      // Kick background hunt/drain only — do NOT await process-pending here
      // (slow batches cause nginx/gateway timeouts; client can call process-pending separately).
      const hunt = await startItyProxyHunt();
      const download = await fetchItyDownloadStatus().catch(() => null);
      const pending = await fetchItyPendingDownloads(
        Math.min(body.batchSize ?? 3, 5),
      ).catch(() => ({ count: download?.pendingCount ?? 0, items: [] }));

      const pendingCount = download?.pendingCount ?? pending.count;
      const progress = await noteDownloadEvent(
        "hunting",
        `Đã kích proxy-hunt/drain · pending ~${pendingCount}`,
      ).catch(() => null);

      return NextResponse.json({
        ok: true,
        action,
        hunt,
        processed: null,
        processError: null,
        pendingCount,
        serverCanDownload: download?.serverCanDownload ?? null,
        progress,
        hint: "Proxy-hunt/drain đã kích nền trên ChốtKiểm. Dùng process-pending / bảng tốc độ để theo dõi.",
      });
    }

    if (action === "process-pending") {
      let callIds = Array.isArray(body.callIds)
        ? body.callIds.filter((id) => typeof id === "string" && id.length > 0)
        : [];

      if (!callIds.length) {
        const pending = await fetchItyPendingDownloads(body.batchSize ?? 8);
        callIds = pending.items.map((i) => i.callId);
      }

      if (!callIds.length) {
        const download = await fetchItyDownloadStatus();
        return NextResponse.json({
          ok: true,
          action,
          processed: { ok: true, processed: 0 },
          pendingCount: download.pendingCount,
          message: "Hàng chờ ghi âm trống",
        });
      }

      try {
        const concurrency = Math.max(1, Math.min(Number(body.concurrency ?? 3), 4));
        const batchIds = callIds.slice(0, 8);
        // Default ON: download success => immediate local deep analysis persist.
        const immediate = await downloadThenAnalyzeCalls({
          callIds: batchIds,
          autoAnalyze: body.autoAnalyze !== false,
          concurrency,
          timeoutMs: 45_000,
        });
        const download = await fetchItyDownloadStatus().catch(() => null);
        const progress = await noteDownloadEvent(
          "downloading",
          `Tải+phân tích x${batchIds.length} · sâu ${immediate.analyze.analyzed} · còn ~${download?.pendingCount ?? "?"}`,
        ).catch(() => null);
        return NextResponse.json({
          ok: true,
          action,
          processed: immediate.download,
          analyze: immediate.analyze,
          timedOut: immediate.timedOut,
          error: immediate.downloadError,
          pendingCount: download?.pendingCount ?? null,
          batchSize: batchIds.length,
          concurrency,
          progress,
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown error";
        const download = await fetchItyDownloadStatus().catch(() => null);
        return NextResponse.json({
          ok: true,
          action,
          timedOut: true,
          error: message,
          pendingCount: download?.pendingCount ?? null,
          batchSize: callIds.length,
          hint: "ChốtKiểm có thể vẫn tải nền qua proxy-hunt — tiếp tục poll status.",
        });
      }
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
