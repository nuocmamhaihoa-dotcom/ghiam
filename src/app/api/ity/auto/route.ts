import { NextRequest, NextResponse } from "next/server";
import {
  getAutoPipelineState,
  runAutoPipelineTick,
  setAutoPipelineEnabled,
  updateAutoPipelineConfig,
  type AutoPipelineConfig,
} from "@/lib/ityAutoPipeline";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

/** GET: auto-pipeline status (enabled, last tick, recent log). */
export async function GET() {
  try {
    const state = await getAutoPipelineState();
    return NextResponse.json({ ok: true, state });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ ok: false, error: message }, { status: 502 });
  }
}

type AutoAction = {
  action?: "status" | "enable" | "disable" | "tick" | "configure";
  force?: boolean;
  config?: Partial<AutoPipelineConfig>;
};

/**
 * POST:
 * - enable / disable continuous mode
 * - tick: run one auto cycle (worker calls this forever)
 * - configure: tune interval / batch / import cadence
 */
export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as AutoAction;
    const action = body.action || "status";

    if (action === "status") {
      const state = await getAutoPipelineState();
      return NextResponse.json({ ok: true, action, state });
    }

    if (action === "enable") {
      const state = await setAutoPipelineEnabled(true);
      // Kick an immediate cycle so user sees activity right away.
      const { result } = await runAutoPipelineTick(true);
      return NextResponse.json({ ok: true, action, state, result });
    }

    if (action === "disable") {
      const state = await setAutoPipelineEnabled(false);
      return NextResponse.json({ ok: true, action, state });
    }

    if (action === "configure") {
      const state = await updateAutoPipelineConfig(body.config || {});
      return NextResponse.json({ ok: true, action, state });
    }

    if (action === "tick") {
      const { state, result } = await runAutoPipelineTick(body.force === true);
      return NextResponse.json({ ok: true, action, state, result });
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
