"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function AutonomousPage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null);
  const [nba, setNba] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);

  async function loadDashboard() {
    const res = await api.autonomousDashboard();
    setDashboard(res.data);
    setSource(res.source);
  }

  async function runNba() {
    const res = await api.autonomousNba({
      buy_signal: 0.82,
      sentiment: "positive",
      intent: "ready",
      auto_execute: true,
    });
    setNba(res.data);
    setSource(res.source);
  }

  async function loadQuality() {
    const res = await api.autonomousQuality();
    setQuality(res.data);
    setSource(res.source);
  }

  const widgets = (dashboard?.widgets as Record<string, unknown> | undefined) || {};
  const pending =
    ((dashboard?.pending_approvals as Record<string, unknown>[] | undefined) || []) as Record<
      string,
      unknown
    >[];
  const checks = (quality?.checks as Record<string, Record<string, unknown>> | undefined) || {};
  const rec = (nba?.recommendation as Record<string, unknown> | undefined) || {};
  const job = (nba?.automation as Record<string, unknown> | undefined) || {};

  return (
    <AppShell>
      <PageHeader
        title="Autonomous Sales AI"
        description="Next Best Action, safe automations, and approval-gated rule/SOP/pricing changes."
        actions={<SourcePill source={source} />}
      />
      <div className="mb-4 flex flex-wrap gap-2">
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadDashboard}>
          Load Dashboard
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={runNba}>
          Run NBA
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadQuality}>
          Quality Gate
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Panel title="Autonomous Actions"><p className="text-2xl font-semibold">{String(widgets.autonomous_actions ?? widgets.recommendation_count ?? "—")}</p></Panel>
        <Panel title="Revenue Impact"><p className="text-2xl font-semibold">{String(widgets.revenue_impact ?? "—")}</p></Panel>
        <Panel title="AI Accuracy"><p className="text-2xl font-semibold">{String(widgets.ai_accuracy ?? widgets.avg_confidence ?? "—")}</p></Panel>
        <Panel title="Approval Queue"><p className="text-2xl font-semibold">{String(widgets.approval_queue ?? widgets.approvals_pending ?? "—")}</p></Panel>
        <Panel title="Knowledge Growth"><p className="text-2xl font-semibold">{String(widgets.knowledge_growth ?? "—")}</p></Panel>
        <Panel title="Forecast"><p className="text-2xl font-semibold">{String(widgets.forecast ?? "—")}</p></Panel>
        <Panel title="Risk"><p className="text-2xl font-semibold">{String(widgets.risk ?? "—")}</p></Panel>
        <Panel title="Automations Run"><p className="text-2xl font-semibold">{String(widgets.automations_run ?? "—")}</p></Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Latest NBA">
          <ul className="space-y-1 text-sm">
            <li>Action: {String(rec.action ?? "—")}</li>
            <li>Confidence: {String(rec.confidence ?? "—")}</li>
            <li>Automation: {String(job.kind ?? "—")} ({String(job.status ?? "—")})</li>
            <li>Approval-only rule changes: {String(dashboard?.approval_only_rule_changes ?? true)}</li>
          </ul>
        </Panel>
        <Panel title="Pending Approvals">
          <ul className="space-y-2 text-sm">
            {pending.slice(0, 8).map((a, idx) => (
              <li key={String(a.approval_id ?? idx)} className="rounded border border-slate-200 px-3 py-2">
                <strong>{String(a.change_type)}</strong> — {String(a.title)} ({String(a.status)})
              </li>
            ))}
            {!pending.length ? <li>—</li> : null}
          </ul>
        </Panel>
      </div>

      <div className="mt-4">
        <Panel title="Quality">
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
    </AppShell>
  );
}
