import { NextRequest, NextResponse } from "next/server";
import {
  analyzePendingRecordings,
  getLibraryStats,
  listRecordings,
  reanalyzeRecordings,
  syncRecordingsFromChotKiem,
} from "@/lib/recordingLibrary";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

/** GET: list local recording library + stats. */
export async function GET(req: NextRequest) {
  try {
    const limit = Math.max(
      1,
      Math.min(Number(req.nextUrl.searchParams.get("limit") || 50), 200),
    );
    const [items, stats] = await Promise.all([
      listRecordings(),
      getLibraryStats(),
    ]);
    return NextResponse.json({
      ok: true,
      stats,
      items: items.slice(0, limit),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  }
}

/** POST: sync/import, analyze pending, or force-reanalyze library. */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as {
      action?: "sync" | "reanalyze" | "analyze-pending";
      limit?: number;
      downloadAudio?: boolean;
      force?: boolean;
      pendingOnly?: boolean;
    };
    const action = body.action || "sync";

    if (action === "analyze-pending") {
      const result = await analyzePendingRecordings({
        limit: body.limit ?? 100,
      });
      const stats = await getLibraryStats();
      return NextResponse.json({ ok: true, action, result, stats });
    }

    if (action === "reanalyze") {
      const result = await reanalyzeRecordings({
        limit: body.limit ?? 100,
        force: body.force === true,
        pendingOnly: body.force === true ? false : body.pendingOnly !== false,
      });
      const stats = await getLibraryStats();
      return NextResponse.json({ ok: true, action, result, stats });
    }

    const result = await syncRecordingsFromChotKiem({
      limit: body.limit ?? 20,
      downloadAudio: body.downloadAudio !== false,
    });
    const stats = await getLibraryStats();
    return NextResponse.json({ ok: true, action: "sync", result, stats });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}
