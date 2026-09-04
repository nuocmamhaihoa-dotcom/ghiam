import { NextRequest, NextResponse } from "next/server";
import {
  getLibraryStats,
  listRecordings,
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

/** POST: sync/import recordings from ChốtKiểm + deep analysis. */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as {
      limit?: number;
      downloadAudio?: boolean;
    };
    const result = await syncRecordingsFromChotKiem({
      limit: body.limit ?? 20,
      downloadAudio: body.downloadAudio !== false,
    });
    const stats = await getLibraryStats();
    return NextResponse.json({ ok: true, result, stats });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}
