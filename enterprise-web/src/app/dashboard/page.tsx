"use client";

import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { FunnelChart } from "@/components/FunnelChart";
import { OrgEmotionTimeline } from "@/components/OrgEmotionTimeline";
import { ParetoChart } from "@/components/ParetoChart";
import { RevenueLeakChart } from "@/components/RevenueLeakChart";
import { StageHeatmap } from "@/components/StageHeatmap";
import { Badge, KpiCard, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import {
  formatNumber,
  formatPercentDisplay,
  formatVnd,
  scoreTone,
} from "@/lib/format";
import type { CallSummary, DashboardOverview } from "@/lib/types";

type RevenueRollup = {
  estimated_total: number;
  currency: string;
  insufficient_evidence_calls: number;
  top_components: {
    cause_code: string;
    amount: number;
    call_count: number;
  }[];
};

function badgeTone(
  score: number
): "slate" | "teal" | "amber" | "rose" | "emerald" | "sky" {
  if (score >= 80) return "emerald";
  if (score >= 60) return "sky";
  if (score >= 40) return "amber";
  return "rose";
}

export default function DashboardPage() {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [leak, setLeak] = useState<RevenueRollup | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [ov, callPack, leakPack] = await Promise.all([
          api.getDashboardOverview(),
          api.listCalls({ limit: 12 }),
          api.getRevenueLeakSummary(),
        ]);
        if (cancelled) return;
        setOverview(ov.data);
        setCalls(callPack.data.data);
        setLeak(leakPack.data);
        setSource(
          ov.source === "api" ||
            callPack.source === "api" ||
            leakPack.source === "api"
            ? "api"
            : "demo"
        );
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Không tải được dashboard");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const employees = useMemo(
    () => overview?.employee_scores ?? [],
    [overview]
  );

  return (
    <AppShell>
      <PageHeader
        title="Operations Dashboard"
        description="Điểm nhân viên · Heatmap · Emotion · Root Cause · Revenue Leak · Coaching · KPI · Pareto · Funnel"
        actions={<SourcePill source={source} />}
      />

      {error ? (
        <div className="mb-4 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      ) : null}

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {(overview?.kpis ?? []).map((kpi) => (
          <KpiCard
            key={kpi.key}
            label={kpi.label}
            value={
              kpi.unit === "VND" || kpi.unit === "vnd"
                ? formatVnd(Number(kpi.value))
                : kpi.unit === "%"
                  ? formatPercentDisplay(Number(kpi.value))
                  : formatNumber(Number(kpi.value))
            }
            delta={kpi.delta_pct}
            hint={kpi.unit}
          />
        ))}
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Stage Heatmap">
          <StageHeatmap rows={overview?.stage_heatmap ?? []} />
        </Panel>
        <Panel title="Conversion Funnel">
          <FunnelChart items={overview?.funnel ?? []} />
        </Panel>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Root Cause Pareto">
          <ParetoChart items={overview?.pareto ?? []} />
        </Panel>
        <Panel title="Org Emotion Timeline">
          <OrgEmotionTimeline points={overview?.emotion_timeline ?? []} />
        </Panel>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Revenue Leak">
          <RevenueLeakChart rollup={leak ?? undefined} />
          <p className="mt-3 text-xs text-slate-500">
            IE leak calls: {formatNumber(overview?.ie_leak_calls ?? 0)} · Window{" "}
            {overview?.window.from?.slice(0, 10)} → {overview?.window.to?.slice(0, 10)}
          </p>
        </Panel>
        <Panel title="Coaching Highlights">
          <div className="space-y-2">
            {(overview?.coaching_highlights ?? []).map((item) => (
              <div
                key={item.plan_id}
                className="flex items-start justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2"
              >
                <div>
                  <div className="text-sm font-medium text-slate-100">{item.title}</div>
                  <div className="text-xs text-slate-500">
                    Plan {item.plan_id}
                    {item.agent_user_id ? ` · agent ${item.agent_user_id}` : ""}
                  </div>
                </div>
                <Badge tone={item.priority === "critical" ? "rose" : "amber"}>
                  {item.status}
                </Badge>
              </div>
            ))}
            {!overview?.coaching_highlights?.length ? (
              <p className="text-sm text-slate-500">Chưa có coaching highlight.</p>
            ) : null}
          </div>
        </Panel>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Top Failed Rules">
          <div className="space-y-2">
            {(overview?.top_failed_rules ?? []).map((row) => (
              <div
                key={row.rule_code}
                className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 px-3 py-2"
              >
                <div>
                  <div className="font-mono text-xs text-teal-300">{row.rule_code}</div>
                  <div className="text-sm text-slate-200">{row.title}</div>
                </div>
                <Badge tone="rose">{row.fail_count}</Badge>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Top Root Causes">
          <div className="space-y-2">
            {(overview?.top_root_causes ?? []).map((row) => (
              <div
                key={row.cause_code}
                className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 px-3 py-2"
              >
                <div>
                  <div className="font-mono text-xs text-amber-300">{row.cause_code}</div>
                  <div className="text-sm text-slate-200">{row.label}</div>
                </div>
                <Badge tone="amber">{row.count}</Badge>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="mb-4">
        <Panel title="Điểm theo nhân viên">
          <DataTable
            rows={employees}
            rowKey={(row) => row.user_id}
            columns={[
              { key: "name", header: "Nhân viên", render: (r) => r.full_name },
              { key: "team", header: "Team", render: (r) => r.team_name || "—" },
              {
                key: "score",
                header: "Avg score",
                render: (r) => (
                  <Badge tone={badgeTone(r.avg_score)}>
                    <span className={scoreTone(r.avg_score)}>{r.avg_score.toFixed(1)}</span>
                  </Badge>
                ),
              },
              { key: "calls", header: "Calls", render: (r) => formatNumber(r.calls) },
              { key: "leak", header: "Leak", render: (r) => formatVnd(r.leak_vnd) },
              {
                key: "ie",
                header: "IE rate",
                render: (r) => formatPercentDisplay(r.ie_rate),
              },
            ]}
          />
        </Panel>
      </div>

      <div className="mb-4">
        <Panel title="Recent Calls">
          <DataTable
            rows={calls}
            rowKey={(row) => row.id}
            columns={[
              { key: "code", header: "Call", render: (r) => r.external_call_id },
              { key: "agent", header: "Agent", render: (r) => r.agent_name },
              { key: "campaign", header: "Campaign", render: (r) => r.campaign_code },
              {
                key: "score",
                header: "Score",
                render: (r) =>
                  r.overall_score == null ? (
                    "—"
                  ) : (
                    <Badge tone={badgeTone(r.overall_score)}>{r.overall_score}</Badge>
                  ),
              },
              {
                key: "status",
                header: "Status",
                render: (r) => <Badge>{r.status}</Badge>,
              },
            ]}
          />
        </Panel>
      </div>

      <Panel title="Snapshot">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard label="Calls" value={formatNumber(overview?.calls_total ?? 0)} />
          <KpiCard label="Scored" value={formatNumber(overview?.scored ?? 0)} />
          <KpiCard label="Avg score" value={(overview?.avg_score ?? 0).toFixed(1)} />
          <KpiCard
            label="Auto-fail rate"
            value={formatPercentDisplay(overview?.auto_fail_rate ?? 0)}
          />
        </div>
      </Panel>
    </AppShell>
  );
}
