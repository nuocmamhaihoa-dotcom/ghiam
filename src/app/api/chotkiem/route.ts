import { NextResponse } from "next/server";
import {
  fetchChotKiemSampleCalls,
  fetchChotKiemStats,
  maskPhone,
} from "@/lib/chotKiemClient";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Live dashboard snapshot from ChốtKiểm. */
export async function GET() {
  try {
    const stats = await fetchChotKiemStats();
    return NextResponse.json({ ok: true, stats });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}

/**
 * Pull a small sample of real calls (transcript + meta) for coaching import.
 * Phones are masked before leaving the server.
 */
export async function POST() {
  try {
    const [stats, calls] = await Promise.all([
      fetchChotKiemStats(),
      fetchChotKiemSampleCalls(8),
    ]);

    const samples = calls.map((c) => ({
      externalId: c.id,
      title: `ChốtKiểm · ${c.employeeName || "Sale"} · ${c.grade || "?"}`,
      industry: "Chốt đơn (live)",
      product: (c.callSummary || "Sản phẩm").split("·")[0]?.replace(/^SP:\s*/i, "").trim() || "Sản phẩm",
      agentName: c.employeeName || "Sale",
      outcome:
        c.grade === "A" && c.isComplete
          ? ("won" as const)
          : c.grade === "F"
            ? ("lost" as const)
            : ("callback" as const),
      durationSec: Math.max(45, Math.min(600, Math.round((c.transcript?.length || 200) / 8))),
      transcript: c.transcript || "",
      phoneMasked: maskPhone(c.phoneNumber),
      liveGrade: c.grade || "?",
      liveScore: c.overallScore ?? 0,
      liveSummary: c.callSummary || "",
      source: "chotkiem" as const,
    }));

    return NextResponse.json({
      ok: true,
      stats,
      imported: samples.length,
      samples,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}
