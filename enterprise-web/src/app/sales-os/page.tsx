"use client";

import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

const ROLES = ["CEO", "Sales Director", "Team Leader", "QA", "Telesale"] as const;

const DEMO_AGENTS = [
  {
    agent_id: "A1",
    name: "An",
    telesale_skill: 0.92,
    conversation_dna: "consultative",
    close_rate: 0.31,
    industry_experience: ["banking", "insurance"],
    peak_hours: [9, 10, 11, 14, 15, 16],
    workload: 3,
    active: true,
  },
  {
    agent_id: "A2",
    name: "Binh",
    telesale_skill: 0.7,
    conversation_dna: "assertive",
    close_rate: 0.22,
    industry_experience: ["telecom"],
    peak_hours: [10, 11, 14],
    workload: 9,
    active: true,
  },
  {
    agent_id: "A3",
    name: "Chi",
    telesale_skill: 0.85,
    conversation_dna: "empathic",
    close_rate: 0.28,
    industry_experience: ["banking"],
    peak_hours: [9, 10, 15, 16, 17],
    workload: 5,
    active: true,
  },
];

const DEMO_LEAD = {
  lead_id: "LEAD-1001",
  industry: "banking",
  urgency: 0.82,
  value: 95_000_000,
  dna_preference: "consultative",
  source: "hubspot",
  preferred_hour: 10,
};

const DEMO_CALL = {
  lead_id: "LEAD-1001",
  call_id: "CALL-88",
  outcome: "interested",
  buy_signals: 3,
  sentiment: 0.8,
  attempts: 2,
  score: 78,
  agent_id: "A1",
};

const DEMO_HISTORY = [
  { conversion_rate: 0.18, revenue: 90_000_000 },
  { conversion_rate: 0.2, revenue: 100_000_000 },
  { conversion_rate: 0.22, revenue: 110_000_000 },
  { conversion_rate: 0.21, revenue: 105_000_000 },
  { conversion_rate: 0.24, revenue: 120_000_000 },
  { conversion_rate: 0.23, revenue: 118_000_000 },
];

export default function SalesOSPage() {
  const [role, setRole] = useState<(typeof ROLES)[number]>("CEO");
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null);
  const [route, setRoute] = useState<Record<string, unknown> | null>(null);
  const [nba, setNba] = useState<Record<string, unknown> | null>(null);
  const [forecast, setForecast] = useState<Record<string, unknown> | null>(null);
  const [sync, setSync] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const widgets = useMemo(() => {
    const w = (dashboard?.widgets as Record<string, unknown> | undefined) || {};
    return Object.entries(w);
  }, [dashboard]);

  async function loadDashboard() {
    setError(null);
    const res = await api.salesOsDashboard(role);
    setDashboard(res.data);
    setSource(res.source);
  }

  async function runRoute() {
    setError(null);
    const res = await api.salesOsRoute(DEMO_LEAD, DEMO_AGENTS, 10);
    setRoute(res.data);
    setSource(res.source);
  }

  async function runNba() {
    setError(null);
    const res = await api.salesOsNextBestAction(DEMO_CALL, true);
    setNba(res.data);
    setSource(res.source);
  }

  async function runForecast() {
    setError(null);
    const res = await api.salesOsForecast(DEMO_HISTORY, [
      { value: 80_000_000, stage: "proposal", days_in_stage: 4 },
      { value: 40_000_000, stage: "negotiation", days_in_stage: 12 },
    ]);
    setForecast(res.data);
    setSource(res.source);
  }

  async function runSync() {
    setError(null);
    const res = await api.salesOsSyncAll({ records: 5, dry_run: true });
    setSync(res.data);
    setSource(res.source);
  }

  async function runQuality() {
    setError(null);
    const res = await api.salesOsQuality();
    setQuality(res.data);
    setSource(res.source);
  }

  return (
    <AppShell>
      <PageHeader
        title="AI Sales Operating System"
        description="Lớp điều phối trung tâm: CRM · Tổng đài · AI Routing · Forecast · Automation · Dashboard doanh nghiệp."
        actions={<SourcePill source={source} />}
      />

      {error ? (
        <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      <div className="mb-4 flex flex-wrap gap-2">
        {ROLES.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => setRole(r)}
            className={`rounded-lg px-3 py-1.5 text-sm ${
              role === r ? "bg-teal-400 text-slate-950" : "bg-slate-800 text-slate-200"
            }`}
          >
            {r}
          </button>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        <button type="button" onClick={() => void loadDashboard()} className="rounded-lg bg-teal-500/90 px-3 py-2 text-sm text-slate-950">
          Load Dashboard
        </button>
        <button type="button" onClick={() => void runRoute()} className="rounded-lg bg-slate-700 px-3 py-2 text-sm">
          AI Lead Routing
        </button>
        <button type="button" onClick={() => void runNba()} className="rounded-lg bg-slate-700 px-3 py-2 text-sm">
          Next Best Action
        </button>
        <button type="button" onClick={() => void runForecast()} className="rounded-lg bg-slate-700 px-3 py-2 text-sm">
          Forecast
        </button>
        <button type="button" onClick={() => void runSync()} className="rounded-lg bg-slate-700 px-3 py-2 text-sm">
          CRM Sync All
        </button>
        <button type="button" onClick={() => void runQuality()} className="rounded-lg bg-slate-700 px-3 py-2 text-sm">
          Quality Gate
        </button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title={`Dashboard · ${role}`}>
          {widgets.length === 0 ? (
            <p className="text-sm text-slate-400">Chưa load dashboard.</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {widgets.map(([name, data]) => (
                <div key={name} className="rounded-lg border border-slate-700/80 bg-slate-950/50 p-3">
                  <div className="mb-1 text-xs uppercase tracking-wide text-teal-300/90">{name}</div>
                  <pre className="overflow-auto text-[11px] text-slate-300">{JSON.stringify(data, null, 2)}</pre>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="AI Lead Routing">
          <pre className="overflow-auto text-xs text-slate-300">
            {route ? JSON.stringify(route, null, 2) : "Chưa chạy routing."}
          </pre>
        </Panel>

        <Panel title="Next Best Action">
          <pre className="overflow-auto text-xs text-slate-300">
            {nba ? JSON.stringify(nba, null, 2) : "Chưa chạy NBA."}
          </pre>
        </Panel>

        <Panel title="Sales Forecast">
          <pre className="overflow-auto text-xs text-slate-300">
            {forecast ? JSON.stringify(forecast, null, 2) : "Chưa forecast."}
          </pre>
        </Panel>

        <Panel title="CRM / Connector Sync">
          <pre className="overflow-auto text-xs text-slate-300">
            {sync ? JSON.stringify(sync, null, 2) : "Chưa sync."}
          </pre>
        </Panel>

        <Panel title="Quality Snapshot">
          <pre className="overflow-auto text-xs text-slate-300">
            {quality ? JSON.stringify(quality, null, 2) : "Chưa chạy quality."}
          </pre>
        </Panel>
      </div>
    </AppShell>
  );
}
