"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function SimulatorPage() {
  const [scenarios, setScenarios] = useState<Record<string, unknown>[]>([]);
  const [session, setSession] = useState<Record<string, unknown> | null>(null);
  const [reply, setReply] = useState("Em hiểu giá là điểm cân nhắc. Gói này gồm bảo hành 12 tháng và hỗ trợ đổi trả.");
  const [grade, setGrade] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");

  useEffect(() => {
    void (async () => {
      const res = await api.listSimulatorScenarios("price");
      setScenarios(res.data.data || []);
      setSource(res.source);
    })();
  }, []);

  async function start() {
    const res = await api.startSimulator("price", 7);
    setSession(res.data);
    setGrade(null);
    setSource(res.source);
  }

  async function gradeReply() {
    const scenario = (session?.scenario || {}) as Record<string, unknown>;
    const id = String(scenario.id || "price_too_high");
    const res = await api.gradeSimulator(id, reply);
    setGrade(res.data);
    setSource(res.source);
  }

  return (
    <AppShell>
      <PageHeader
        title="Objection Simulator"
        description="AI đóng vai khách hàng — chấm điểm ngay sau role-play."
        actions={<SourcePill source={source} />}
      />
      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Scenarios">
          <ul className="space-y-2 text-sm text-slate-300">
            {scenarios.map((s) => (
              <li key={String(s.id)} className="rounded border border-slate-800 px-3 py-2">
                <div className="font-medium text-slate-100">{String(s.id)}</div>
                <div className="text-xs text-slate-400">{String(s.prompt || s.group || "")}</div>
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => void start()} className="mt-3 rounded-lg bg-teal-500/90 px-4 py-2 text-sm text-slate-950">
            Start role-play
          </button>
        </Panel>
        <Panel title="Session">
          <pre className="max-h-72 overflow-auto rounded bg-slate-950/80 p-3 text-xs text-slate-300">
            {session ? JSON.stringify(session, null, 2) : "Chưa start."}
          </pre>
          <textarea
            className="mt-3 h-28 w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm"
            value={reply}
            onChange={(e) => setReply(e.target.value)}
          />
          <button type="button" onClick={() => void gradeReply()} className="mt-2 rounded-lg border border-teal-500/40 px-4 py-2 text-sm text-teal-200">
            Grade reply
          </button>
        </Panel>
        <Panel title="Grade">
          <pre className="overflow-auto rounded bg-slate-950/80 p-3 text-xs text-slate-300">
            {grade ? JSON.stringify(grade, null, 2) : "Chưa chấm."}
          </pre>
        </Panel>
      </div>
    </AppShell>
  );
}
