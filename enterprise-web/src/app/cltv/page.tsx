"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function CLTVPage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [leadId, setLeadId] = useState("lead-demo-001");
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null);
  const [prediction, setPrediction] = useState<Record<string, unknown> | null>(null);
  const [ranked, setRanked] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);

  async function loadDashboard() {
    const res = await api.cltvDashboard();
    setDashboard(res.data);
    setSource(res.source);
  }

  async function runPredict() {
    const res = await api.cltvPredict({
      lead_id: leadId,
      call_history: [{ connected: true, converted: true, qa_score: 88, duration: 180 }],
      conversation_dna: { rapport: 0.8, value_building: 0.75, closing: 0.7 },
      intent: { label: "buy", confidence: 0.85 },
      emotion: "positive",
      buying_signal: 0.8,
      crm: { past_revenue: 2_000_000, segment: "vip", aov: 800_000 },
      follow_up: { completed: 2, missed: 0 },
    });
    setPrediction(res.data);
    setSource(res.source);
  }

  async function runPrioritize() {
    const res = await api.cltvPrioritize({
      leads: [
        {
          lead_id: "A",
          call_history: [{ connected: true, converted: true, qa_score: 90 }],
          buying_signal: 0.9,
          crm: { segment: "vip", past_revenue: 3_000_000 },
        },
        {
          lead_id: "B",
          call_history: [{ connected: false }],
          buying_signal: 0.2,
          emotion: "frustrated",
        },
        {
          lead_id: "C",
          call_history: [{ connected: true, qa_score: 70 }],
          buying_signal: 0.5,
          crm: { segment: "standard", past_revenue: 500_000 },
        },
      ],
    });
    setRanked(res.data);
    setSource(res.source);
  }

  async function loadQuality() {
    const res = await api.cltvQuality();
    setQuality(res.data);
    setSource(res.source);
  }

  const widgets = (dashboard?.widgets as Record<string, unknown> | undefined) || {};
  const scores = (prediction?.scores as Record<string, unknown> | undefined) || {};
  const checks = (quality?.checks as Record<string, Record<string, unknown>> | undefined) || {};
  const rankedRows = (ranked?.ranked as Record<string, unknown>[] | undefined) || [];

  return (
    <AppShell>
      <PageHeader
        title="CLTV Engine"
        description="Dự báo Customer Lifetime Value, churn, upsell/cross-sell và ưu tiên lead theo giá trị dài hạn."
        actions={<SourcePill source={source} />}
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadDashboard}>
          Load Dashboard
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={runPredict}>
          Predict CLTV
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={runPrioritize}>
          Prioritize Leads
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadQuality}>
          Quality Gate
        </button>
      </div>

      <Panel title="Lead ID">
        <input
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
          value={leadId}
          onChange={(e) => setLeadId(e.target.value)}
        />
      </Panel>

      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Panel title="CLTV Forecast">
          <p className="text-2xl font-semibold">{String(widgets.cltv_forecast ?? "—")}</p>
        </Panel>
        <Panel title="Churn Forecast">
          <p className="text-2xl font-semibold">{String(widgets.churn_forecast ?? "—")}</p>
        </Panel>
        <Panel title="Avg Lifetime Value">
          <p className="text-2xl font-semibold">{String(widgets.avg_lifetime_value ?? "—")}</p>
        </Panel>
        <Panel title="Predictions">
          <p className="text-2xl font-semibold">{String(widgets.prediction_count ?? "—")}</p>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Prediction">
          <p className="text-sm">Priority: <strong>{String(prediction?.priority ?? "—")}</strong></p>
          <p className="text-sm">Lifetime Value: {String(prediction?.lifetime_value ?? "—")}</p>
          <p className="text-sm">Churn Risk: {String(prediction?.churn_risk ?? "—")}</p>
          <p className="mt-2 text-xs text-slate-600">CLTV score: {String(scores.cltv_score ?? "—")}</p>
          <p className="text-xs text-slate-600">Upsell: {String(scores.upsell_score ?? "—")} · Cross-sell: {String(scores.cross_sell_score ?? "—")}</p>
        </Panel>
        <Panel title="Quality Checks">
          <ul className="space-y-1 text-sm">
            {Object.entries(checks).map(([k, v]) => (
              <li key={k}>
                {k}: {String(v?.ok ? "PASS" : "FAIL")} ({String(v?.value ?? "—")})
              </li>
            ))}
            {!Object.keys(checks).length ? <li>—</li> : null}
          </ul>
        </Panel>
      </div>

      <div className="mt-4">
        <Panel title="Prioritized Leads">
          <ul className="space-y-2 text-sm">
            {rankedRows.slice(0, 8).map((row, idx) => {
              const s = (row.scores as Record<string, unknown> | undefined) || {};
              return (
                <li key={String(row.lead_id ?? idx)} className="rounded border border-slate-200 px-3 py-2">
                  #{idx + 1} {String(row.lead_id)} — {String(s.priority)} · CLTV {String(s.cltv_score)} · LTV {String(s.lifetime_value)}
                </li>
              );
            })}
            {!rankedRows.length ? <li>—</li> : null}
          </ul>
        </Panel>
      </div>
    </AppShell>
  );
}
