"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

const SAMPLE = [
  { speaker: "customer", text: "Gói này có bảo hành không? Nhà còn xe máy nữa.", start: 0, end: 4 },
  { speaker: "agent", text: "Dạ có bảo hành 12 tháng ạ.", start: 4, end: 7 },
];

export default function MultiProductPage() {
  const [turnsJson, setTurnsJson] = useState(JSON.stringify(SAMPLE, null, 2));
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");

  async function run() {
    const turns = JSON.parse(turnsJson) as Record<string, unknown>[];
    const res = await api.suggestMultiProduct(turns, "PKG-HEALTH");
    setResult(res.data);
    setSource(res.source);
  }

  return (
    <AppShell>
      <PageHeader
        title="Multi-Product Intelligence"
        description="Cross-sell / upsell / bundle — đề xuất sản phẩm tiếp theo có căn cứ hội thoại."
        actions={<SourcePill source={source} />}
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Turns">
          <textarea className="h-56 w-full rounded border border-slate-700 bg-slate-950 p-3 font-mono text-xs" value={turnsJson} onChange={(e) => setTurnsJson(e.target.value)} />
          <button type="button" onClick={() => void run()} className="mt-3 rounded-lg bg-teal-500/90 px-4 py-2 text-sm text-slate-950">Suggest</button>
        </Panel>
        <Panel title="Suggestions">
          <pre className="overflow-auto rounded bg-slate-950/80 p-3 text-xs text-slate-300">{result ? JSON.stringify(result, null, 2) : "Chưa suggest."}</pre>
        </Panel>
      </div>
    </AppShell>
  );
}
