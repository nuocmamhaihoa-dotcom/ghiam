"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function MemoryGraphPage() {
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");

  async function run() {
    const res = await api.buildMemoryGraph({
      call_id: "demo-call-1",
      violations: [{ rule_id: "R-OBJ-01", severity: "major" }],
      root_cause: { primary_cause_code: "RC-PRICE" },
      coaching: { tips: [{ title: "Reframe value" }] },
      intents: [{ name: "price_concern" }],
      objections: [{ type: "price" }],
      products: ["PKG-HEALTH"],
    });
    setResult(res.data);
    setSource(res.source);
  }

  return (
    <AppShell>
      <PageHeader
        title="Memory Graph"
        description="Knowledge graph liên kết call ↔ rule ↔ root cause ↔ coaching ↔ intent ↔ objection ↔ product."
        actions={<SourcePill source={source} />}
      />
      <Panel title="Build from analysis">
        <button type="button" onClick={() => void run()} className="rounded-lg bg-teal-500/90 px-4 py-2 text-sm text-slate-950">
          Build graph
        </button>
        <pre className="mt-3 max-h-[480px] overflow-auto rounded-lg bg-slate-950/80 p-3 text-xs text-slate-300">
          {result ? JSON.stringify(result, null, 2) : "Chưa build."}
        </pre>
      </Panel>
    </AppShell>
  );
}
