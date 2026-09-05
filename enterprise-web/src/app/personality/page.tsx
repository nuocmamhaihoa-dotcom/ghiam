"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

const SAMPLE = [
  { speaker: "customer", text: "Cho em xem thông số và so sánh với bên kia rõ ràng.", start: 0, end: 4 },
  { speaker: "customer", text: "Bảo hành thế nào? Hợp đồng điều khoản ra sao?", start: 4, end: 8 },
];

export default function PersonalityPage() {
  const [turnsJson, setTurnsJson] = useState(JSON.stringify(SAMPLE, null, 2));
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");

  async function run() {
    const turns = JSON.parse(turnsJson) as Record<string, unknown>[];
    const res = await api.analyzePersonality(turns);
    setResult(res.data);
    setSource(res.source);
  }

  return (
    <AppShell>
      <PageHeader
        title="Customer Personality"
        description="DISC / buyer type / communication style + suggested & forbidden scripts."
        actions={<SourcePill source={source} />}
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Customer turns">
          <textarea
            className="h-56 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs"
            value={turnsJson}
            onChange={(e) => setTurnsJson(e.target.value)}
          />
          <button type="button" onClick={() => void run()} className="mt-3 rounded-lg bg-teal-500/90 px-4 py-2 text-sm text-slate-950">
            Phân tích
          </button>
        </Panel>
        <Panel title="Personality card">
          <pre className="overflow-auto rounded-lg bg-slate-950/80 p-3 text-xs text-slate-300">
            {result ? JSON.stringify(result, null, 2) : "Chưa chạy."}
          </pre>
        </Panel>
      </div>
    </AppShell>
  );
}
