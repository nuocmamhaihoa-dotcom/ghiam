"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

const SAMPLE = [
  { speaker: "agent", text: "Em cam kết lãi suất 0% dù hồ sơ chưa duyệt.", start: 0, end: 4 },
  { speaker: "customer", text: "Vậy chắc chắn được duyệt chứ?", start: 4, end: 7 },
];

export default function FraudPage() {
  const [turnsJson, setTurnsJson] = useState(JSON.stringify(SAMPLE, null, 2));
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");

  async function run() {
    const turns = JSON.parse(turnsJson) as Record<string, unknown>[];
    const res = await api.scanFraud(turns);
    setResult(res.data);
    setSource(res.source);
  }

  return (
    <AppShell>
      <PageHeader
        title="Fraud & Compliance AI"
        description="Phát hiện hứa sai, báo giá sai, thiếu tư vấn, vi phạm SOP — mọi cảnh báo phải có evidence."
        actions={<SourcePill source={source} />}
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Turns">
          <textarea className="h-56 w-full rounded border border-slate-700 bg-slate-950 p-3 font-mono text-xs" value={turnsJson} onChange={(e) => setTurnsJson(e.target.value)} />
          <button type="button" onClick={() => void run()} className="mt-3 rounded-lg bg-teal-500/90 px-4 py-2 text-sm text-slate-950">Scan</button>
        </Panel>
        <Panel title="Findings">
          <pre className="overflow-auto rounded bg-slate-950/80 p-3 text-xs text-slate-300">{result ? JSON.stringify(result, null, 2) : "Chưa scan."}</pre>
        </Panel>
      </div>
    </AppShell>
  );
}
