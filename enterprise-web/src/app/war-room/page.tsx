"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function WarRoomPage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null);
  const [alerts, setAlerts] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);

  async function loadDashboard() {
    const res = await api.warRoomDashboard();
    setDashboard(res.data);
    setSource(res.source);
  }

  async function scanAlerts() {
    const res = await api.warRoomScanAlerts();
    setAlerts(res.data);
    setSource(res.source);
  }

  async function loadQuality() {
    const res = await api.warRoomQuality();
    setQuality(res.data);
    setSource(res.source);
  }

  const widgets = (dashboard?.widgets as Record<string, unknown> | undefined) || {};
  const recentAlerts =
    ((alerts?.alerts as Record<string, unknown>[] | undefined) ||
      (dashboard?.recent_alerts as Record<string, unknown>[] | undefined) ||
      []) as Record<string, unknown>[];
  const checks = (quality?.checks as Record<string, Record<string, unknown>> | undefined) || {};

  return (
    <AppShell>
      <PageHeader
        title="War Room AI"
        description="Live ops command center — active calls, queue health, alerts, and realtime floor signals."
        actions={<SourcePill source={source} />}
      />
      <div className="mb-4 flex flex-wrap gap-2">
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadDashboard}>
          Load Dashboard
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={scanAlerts}>
          Scan Alerts
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadQuality}>
          Quality Gate
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Panel title="Active Calls"><p className="text-2xl font-semibold">{String(widgets.active_calls ?? "—")}</p></Panel>
        <Panel title="Online Agents"><p className="text-2xl font-semibold">{String(widgets.online_agents ?? "—")}</p></Panel>
        <Panel title="Queue Size"><p className="text-2xl font-semibold">{String(widgets.queue_size ?? "—")}</p></Panel>
        <Panel title="Open Alerts"><p className="text-2xl font-semibold">{String(widgets.open_alerts ?? "—")}</p></Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Live Alerts">
          <ul className="space-y-2 text-sm">
            {recentAlerts.slice(0, 8).map((a, idx) => (
              <li key={String(a.alert_id ?? idx)} className="rounded border border-slate-200 px-3 py-2">
                <strong>{String(a.severity)}</strong> · {String(a.kind)} — {String(a.title || a.message)}
              </li>
            ))}
            {!recentAlerts.length ? <li>—</li> : null}
          </ul>
        </Panel>
        <Panel title="Quality">
          <ul className="space-y-1 text-sm">
            {Object.entries(checks).map(([k, v]) => (
              <li key={k}>{k}: {String(v?.ok ? "PASS" : "FAIL")} ({String(v?.value ?? "—")})</li>
            ))}
            {!Object.keys(checks).length ? <li>—</li> : null}
          </ul>
          <p className="mt-2 text-xs text-slate-600">Realtime: {String(quality?.realtime_enabled ?? dashboard?.realtime_enabled ?? "—")}</p>
        </Panel>
      </div>
    </AppShell>
  );
}
