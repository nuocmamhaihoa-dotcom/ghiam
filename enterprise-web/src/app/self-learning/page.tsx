"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

const DEMO_CALL = {
  call_id: "SL-DEMO-001",
  turns: [
    { speaker: "customer", text: "Để em chuyển khoản tối nhé" },
    { speaker: "agent", text: "Dạ em gửi STK luôn ạ" },
    { speaker: "customer", text: "Chờ hết tháng cô hồn đã" },
    { speaker: "customer", text: "Để em coi đã" },
    { speaker: "customer", text: "Để em xem thêm" },
    { speaker: "customer", text: "Để em cân nhắc" },
  ],
};

export default function SelfLearningPage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null);
  const [ingest, setIngest] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadDashboard() {
    setError(null);
    const res = await api.selfLearningDashboard();
    setDashboard(res.data);
    setSource(res.source);
  }

  async function runIngest() {
    setError(null);
    const res = await api.selfLearningIngest(DEMO_CALL);
    setIngest(res.data);
    setSource(res.source);
  }

  async function loadQuality() {
    setError(null);
    const res = await api.selfLearningQuality();
    setQuality(res.data);
    setSource(res.source);
  }

  const widgets = (dashboard?.widgets as Record<string, unknown> | undefined) || {};
  const layers = (dashboard?.layers as Record<string, number> | undefined) || {};

  return (
    <AppShell>
      <PageHeader
        title="Self-Learning Lab"
        description="AI Research Lab nội bộ — học từ mọi cuộc gọi, không tự sửa Rulebook khi chưa QA duyệt."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <button
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          onClick={loadDashboard}
        >
          Load Dashboard
        </button>
        <button
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          onClick={runIngest}
        >
          Ingest Demo Call
        </button>
        <button
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          onClick={loadQuality}
        >
          Quality Snapshot
        </button>
      </div>

      {error ? (
        <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        {Object.entries(widgets).map(([key, value]) => (
          <Panel key={key} title={key.replaceAll("_", " ")}>
            <p className="text-2xl font-semibold tabular-nums text-slate-50">{String(value)}</p>
          </Panel>
        ))}
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Panel title="Knowledge Layers">
          <ul className="space-y-2 text-sm text-slate-300">
            {Object.entries(layers).map(([k, v]) => (
              <li key={k} className="flex justify-between border-b border-slate-800 pb-1">
                <span>{k}</span>
                <span className="font-medium text-slate-100">{v}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-slate-500">
            Auto-apply to production:{" "}
            <strong className="text-amber-300">
              {dashboard?.auto_apply_blocked ? "BLOCKED" : "n/a"}
            </strong>
          </p>
        </Panel>

        <Panel title="Ingest Result">
          {ingest ? (
            <pre className="max-h-80 overflow-auto text-xs text-slate-300 whitespace-pre-wrap">
              {JSON.stringify(ingest, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-slate-500">Chưa ingest — bấm “Ingest Demo Call”.</p>
          )}
        </Panel>
      </div>

      <div className="mt-4">
        <Panel title="Quality Gate">
          {quality ? (
            <pre className="max-h-60 overflow-auto text-xs text-slate-300 whitespace-pre-wrap">
              {JSON.stringify(quality, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-slate-500">Chưa load quality snapshot.</p>
          )}
        </Panel>
      </div>
    </AppShell>
  );
}
