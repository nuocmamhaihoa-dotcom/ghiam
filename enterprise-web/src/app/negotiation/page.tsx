"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function NegotiationPage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [utterance, setUtterance] = useState("Đắt quá");
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null);
  const [analysis, setAnalysis] = useState<Record<string, unknown> | null>(null);
  const [compare, setCompare] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadDashboard() {
    setError(null);
    const res = await api.negotiationDashboard();
    setDashboard(res.data);
    setSource(res.source);
  }

  async function runAnalyze() {
    setError(null);
    const res = await api.negotiationAnalyze({
      customer_utterance: utterance,
      context: { product: "Gói Pro", benefit: "tiết kiệm dài hạn", monthly: "199k" },
    });
    setAnalysis(res.data);
    setSource(res.source);
  }

  async function runCompare() {
    setError(null);
    const res = await api.negotiationCompare({
      customer_utterance: utterance,
      context: { product: "Gói Pro" },
    });
    setCompare(res.data);
    setSource(res.source);
  }

  async function loadQuality() {
    setError(null);
    const res = await api.negotiationQuality();
    setQuality(res.data);
    setSource(res.source);
  }

  const widgets = (dashboard?.widgets as Record<string, unknown> | undefined) || {};
  const prediction = (analysis?.prediction as Record<string, unknown> | undefined) || {};
  const strategies = (analysis?.strategies as Record<string, unknown>[] | undefined) || [];
  const best = (analysis?.best_strategy as Record<string, unknown> | undefined) || {};
  const comparisons = (compare?.comparisons as Record<string, unknown>[] | undefined) || [];
  const checks = (quality?.checks as Record<string, Record<string, unknown>> | undefined) || {};

  return (
    <AppShell>
      <PageHeader
        title="Negotiation Strategy Engine"
        description="Dự đoán 3–5 bước tiếp theo + sinh đa chiến lược đàm phán (Strategy Graph)."
        actions={<SourcePill source={source} />}
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadDashboard}>
          Load Dashboard
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={runAnalyze}>
          Analyze
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={runCompare}>
          Compare Strategies
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadQuality}>
          Quality Gate
        </button>
      </div>
      {error ? <p className="mb-3 text-sm text-red-600">{error}</p> : null}

      <Panel title="Customer utterance">
        <input
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
          value={utterance}
          onChange={(e) => setUtterance(e.target.value)}
          placeholder="VD: Đắt quá / Để suy nghĩ / Bên kia rẻ hơn"
        />
      </Panel>

      <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Panel title="Win Probability">
          <p className="text-2xl font-semibold">{String(widgets.win_probability ?? "—")}</p>
        </Panel>
        <Panel title="Next Best Action">
          <p className="text-sm text-slate-700">
            {typeof widgets.next_best_action === "object" && widgets.next_best_action
              ? JSON.stringify(widgets.next_best_action)
              : String(widgets.next_best_action ?? analysis?.next_best_action ?? "—")}
          </p>
        </Panel>
        <Panel title="Avg Exit Risk">
          <p className="text-2xl font-semibold">{String(widgets.avg_exit_risk ?? "—")}</p>
        </Panel>
        <Panel title="Sessions">
          <p className="text-2xl font-semibold">{String(widgets.session_count ?? "—")}</p>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Next-step Prediction">
          <ul className="space-y-1 text-sm text-slate-700">
            <li>Next question: {String(prediction.next_question ?? "—")}</li>
            <li>Next objection: {String(prediction.next_objection ?? "—")}</li>
            <li>Next emotion: {String(prediction.next_emotion ?? "—")}</li>
            <li>Exit risk: {String(prediction.exit_risk ?? "—")}</li>
            <li>Buy probability: {String(prediction.buy_probability ?? "—")}</li>
            <li>Horizon steps: {Array.isArray(prediction.horizon) ? prediction.horizon.length : 0}</li>
          </ul>
        </Panel>
        <Panel title="Best Strategy">
          <ul className="space-y-1 text-sm text-slate-700">
            <li>Kind: {String(best.kind ?? "—")}</li>
            <li>Win: {String(best.win_probability ?? "—")}</li>
            <li>Risk: {String(best.risk_score ?? "—")}</li>
            <li>Recommended: {String(best.recommended_script ?? "—")}</li>
            <li>Forbidden: {String(best.forbidden_script ?? "—")}</li>
          </ul>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Strategy Options">
          <div className="space-y-3">
            {strategies.map((s, i) => (
              <div key={String(s.strategy_id ?? i)} className="border-b border-slate-200 pb-2 text-sm">
                <div className="font-medium">
                  {String(s.label ?? s.kind)} · win {String(s.win_probability)} · risk {String(s.risk_score)}
                </div>
                <div className="text-slate-600">✓ {String(s.recommended_script)}</div>
                <div className="text-red-600">✗ {String(s.forbidden_script)}</div>
              </div>
            ))}
            {!strategies.length ? <p className="text-sm text-slate-500">Chưa có phân tích — bấm Analyze.</p> : null}
          </div>
        </Panel>
        <Panel title="Strategy Comparison">
          <div className="space-y-2 text-sm">
            {comparisons.map((c, i) => (
              <div key={i} className="flex justify-between border-b border-slate-100 py-1">
                <span>
                  #{String(c.rank)} {String(c.kind)}
                </span>
                <span>
                  util {String(c.utility)} · win {String(c.win_probability)}
                </span>
              </div>
            ))}
            {!comparisons.length ? <p className="text-slate-500">Chưa có so sánh — bấm Compare Strategies.</p> : null}
          </div>
        </Panel>
      </div>

      <div className="mt-4">
        <Panel title="Quality Gate">
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4 text-sm">
            {Object.entries(checks).map(([k, v]) => (
              <div key={k} className="rounded border border-slate-200 p-2">
                <div className="font-medium">{k}</div>
                <div>
                  {String(v.ok ? "PASS" : "FAIL")} · {String(v.value)}
                </div>
              </div>
            ))}
            {!Object.keys(checks).length ? <p className="text-slate-500">Chưa load quality.</p> : null}
          </div>
        </Panel>
      </div>
    </AppShell>
  );
}
