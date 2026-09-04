import { NextRequest, NextResponse } from "next/server";
import {
  fetchItyDownloadStatus,
  fetchItyPendingDownloads,
  fetchItySettingsSummary,
  processItyPendingDownloads,
  runItySyncRound,
  startItyProxyHunt,
} from "@/lib/itySyncClient";

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
    const [settings, download, pending] = await Promise.all([
      fetchItySettingsSummary(),
      fetchItyDownloadStatus(),
      fetchItyPendingDownloads(5),
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
  autoAnalyze?: boolean;
  callIds?: string[];
};

/**
 * Client-driven ITY sync (avoids single long request timeouts):
 * - sync-round: one metadata page batch (default daysBack=10)
 * - start-downloads: kick proxy-hunt + try a small pending batch
 * - process-pending: download audio for next / given callIds
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
      const hunt = await startItyProxyHunt();
      const pending = await fetchItyPendingDownloads(body.batchSize ?? 5);
      let processed: Record<string, unknown> | null = null;
      let processError: string | null = null;

      if (pending.items.length > 0) {
        try {
          processed = await processItyPendingDownloads({
            callIds: pending.items.map((i) => i.callId),
            autoAnalyze: body.autoAnalyze === true,
            concurrency: 1,
          });
        } catch (error) {
          processError =
            error instanceof Error ? error.message : "process-pending timeout/error";
        }
      }

      const download = await fetchItyDownloadStatus().catch(() => null);

      return NextResponse.json({
        ok: true,
        action,
        hunt,
        processed,
        processError,
        pendingCount: download?.pendingCount ?? pending.count,
        serverCanDownload: download?.serverCanDownload ?? null,
      });
    }

    if (action === "process-pending") {
      let callIds = Array.isArray(body.callIds)
        ? body.callIds.filter((id) => typeof id === "string" && id.length > 0)
        : [];

      if (!callIds.length) {
        const pending = await fetchItyPendingDownloads(body.batchSize ?? 5);
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
        const processed = await processItyPendingDownloads({
          callIds,
          autoAnalyze: body.autoAnalyze === true,
          concurrency: 1,
        });
        const download = await fetchItyDownloadStatus().catch(() => null);
        return NextResponse.json({
          ok: true,
          action,
          processed,
          pendingCount: download?.pendingCount ?? null,
          batchSize: callIds.length,
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
