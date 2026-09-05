"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { RadarDNA } from "@/components/RadarDNA";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import { scoreTone } from "@/lib/format";
import type { ConversationDNA } from "@/lib/types";

export default function DnaPage() {
  const [dna, setDna] = useState<ConversationDNA | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [callId, setCallId] = useState("cl_1001");

  useEffect(() => {
    void (async () => {
      const res = await api.getConversationDna(callId);
      setDna(res.data);
      setSource(res.source);
    })();
  }, [callId]);

  return (
    <AppShell>
      <PageHeader
        title="Conversation DNA"
        description="Radar kỹ năng hội thoại: rapport, discovery, value, objection, close, compliance."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1.5 block text-slate-400">Call ID</span>
          <input
            value={callId}
            onChange={(e) => setCallId(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-sm outline-none ring-teal-500/30 focus:ring-2"
          />
        </label>
      </div>

      {!dna ? (
        <div className="animate-pulse-soft text-slate-400">Đang tải DNA…</div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
          <Panel>
            <RadarDNA dna={dna} size={420} />
          </Panel>
          <Panel title="Chỉ số chiều">
            <ul className="space-y-2">
              {dna.dimensions
                .slice()
                .sort((a, b) => b.score - a.score)
                .map((d) => (
                  <li
                    key={d.key}
                    className="flex items-center justify-between rounded-lg border border-slate-800 px-3 py-2 text-sm"
                  >
                    <span className="text-slate-200">{d.label}</span>
                    <span className={`font-semibold ${scoreTone(d.score)}`}>
                      {Math.round(d.score)}
                    </span>
                  </li>
                ))}
            </ul>
            <p className="mt-4 text-sm leading-relaxed text-slate-400">
              {dna.summary}
            </p>
          </Panel>
        </div>
      )}
    </AppShell>
  );
}
