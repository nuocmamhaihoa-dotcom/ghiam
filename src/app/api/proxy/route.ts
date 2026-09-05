import { NextRequest, NextResponse } from "next/server";
import {
  ensureProxyPool,
  fetchProxyPoolStatus,
  getProxyPoolStore,
} from "@/lib/proxyPool";
import { sampleDownloadProgress } from "@/lib/ityDownloadProgress";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** GET: current living-proxy status + history (auto-ensure by default). */
export async function GET(req: NextRequest) {
  try {
    const auto = req.nextUrl.searchParams.get("auto") !== "0";
    const minLiving = Number(req.nextUrl.searchParams.get("minLiving") || 20);
    const snapshot = auto
      ? await ensureProxyPool({ minLiving: Number.isFinite(minLiving) ? minLiving : 20 })
      : await fetchProxyPoolStatus();
    const store = await getProxyPoolStore();
    const progress = await sampleDownloadProgress().catch(() => null);

    return NextResponse.json({
      ok: true,
      snapshot,
      history: store.history.slice(-60),
      progress,
      livingCount: snapshot.livingCount,
      poolSize: snapshot.poolSize,
      pendingDownloads: snapshot.pendingDownloads,
      fetchedAt: snapshot.fetchedAt,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}

/** POST: force proxy hunt / ensure pool. */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as {
      forceHunt?: boolean;
      minLiving?: number;
    };
    const snapshot = await ensureProxyPool({
      forceHunt: body.forceHunt === true,
      minLiving:
        typeof body.minLiving === "number" && Number.isFinite(body.minLiving)
          ? body.minLiving
          : 20,
    });
    return NextResponse.json({
      ok: true,
      snapshot,
      livingCount: snapshot.livingCount,
      ensureAction: snapshot.ensureAction,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}
