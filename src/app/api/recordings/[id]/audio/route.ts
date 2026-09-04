import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "fs";
import { getRecordingAudioPath } from "@/lib/recordingLibrary";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

/** GET: stream locally saved recording audio. */
export async function GET(_req: NextRequest, ctx: Ctx) {
  try {
    const { id } = await ctx.params;
    const audio = await getRecordingAudioPath(id);
    if (!audio) {
      return NextResponse.json(
        { ok: false, error: "Audio not found" },
        { status: 404 },
      );
    }
    const buf = await fs.readFile(audio.filePath);
    return new NextResponse(buf, {
      status: 200,
      headers: {
        "Content-Type": audio.mime || "audio/wav",
        "Content-Length": String(buf.byteLength),
        "Cache-Control": "private, max-age=3600",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 500 });
  }
}
