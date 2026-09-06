"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

const SAMPLE = [
  { speaker: "agent", text: "Em chào anh, bên em có gói bảo hiểm sức khỏe.", start: 0, end: 3 },
  { speaker: "customer", text: "Giá hơi đắt. Bao giờ giao? Có bảo hành không?", start: 3, end: 8 },
];

export default function LiveAssistantPage() {
  const [turnsJson, setTurnsJson] = useState(JSON.stringify(SAMPLE, null, 2));
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setError(null);
    try {
      const turns = JSON.parse(turnsJson) as Record<string, unknown>[];
      const res = await api.liveAssistantSuggest(turns, Date.now() / 1000);
      setResult(res.data);
      setSource(res.source);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lỗi gọi Live Assistant");
    }
  }

  return (
    <AppShell>
      <PageHeader
        title="Live Call Assistant"
        description="Gợi ý câu hỏi/phản hồi tiếp theo, cảnh báo buying signal / objection / silence. Mục tiêu latency < 2s."
        actions={<SourcePill source={source} />}
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Transcript turns (JSON)">
          <textarea
            className="h-64 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs text-slate-200"
            value={turnsJson}
            onChange={(e) => setTurnsJson(e.target.value)}
          />
          <button
            type="button"
            onClick={() => void run()}
            className="mt-3 rounded-lg bg-teal-500/90 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-teal-400"
          >
            Gợi ý realtime
          </button>
          {error ? <p className="mt-2 text-sm text-rose-300">{error}</p> : null}
        </Panel>
        <Panel title="Assistant output">
          <pre className="overflow-auto rounded-lg bg-slate-950/80 p-3 text-xs text-slate-300">
            {result ? JSON.stringify(result, null, 2) : "Chưa chạy."}
          </pre>
        </Panel>
      </div>
    </AppShell>
  );
}
