import { NextRequest, NextResponse } from "next/server";
import { getRecording } from "@/lib/recordingLibrary";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

/** GET: one recording with deep analysis + recreation script. */
export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const { id } = await ctx.params;
    const rec = await getRecording(id);
    if (!rec) {
      return NextResponse.json(
        { ok: false, error: "Recording not found" },
        { status: 404 },
      );
    }
    return NextResponse.json({ ok: true, recording: rec });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  }
}
