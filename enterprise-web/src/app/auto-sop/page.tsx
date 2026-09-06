"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function AutoSopPage() {
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");

  async function run() {
    const res = await api.generateAutoSop(
      [
        {
          id: "g1",
          score: 94,
          transcript: [
            { speaker: "agent", text: "Em xin phép hỏi nhu cầu chính của anh/chị." },
            { speaker: "customer", text: "Cần bảo hiểm cho gia đình." },
          ],
        },
      ],
      "v1"
    );
    setResult(res.data);
    setSource(res.source);
  }

  return (
    <AppShell>
      <PageHeader
        title="Auto SOP Generator"
        description="Sinh SOP / checklist / rule / coaching từ golden calls — có version history."
        actions={<SourcePill source={source} />}
      />
      <Panel title="Generate">
        <button type="button" onClick={() => void run()} className="rounded-lg bg-teal-500/90 px-4 py-2 text-sm text-slate-950">Generate SOP</button>
        <pre className="mt-3 max-h-[520px] overflow-auto rounded bg-slate-950/80 p-3 text-xs text-slate-300">{result ? JSON.stringify(result, null, 2) : "Chưa generate."}</pre>
      </Panel>
    </AppShell>
  );
}
