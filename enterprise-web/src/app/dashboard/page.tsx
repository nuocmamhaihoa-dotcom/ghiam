"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { RevenueLeakChart } from "@/components/RevenueLeakChart";
import { StageHeatmap } from "@/components/StageHeatmap";
import { KpiCard, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import {
  formatNumber,
  formatPercentDisplay,
  formatVnd,
  scoreTone,
} from "@/lib/format";
import type { DashboardOverview } from "@/lib/types";

export default function DashboardPage() {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [leak, setLeak] = useState<{
    estimated_total: number;
    currency: string;
    insufficient_evidence_calls: number;
    top_components: { cause_code: string; amount: number; call_count: number }[];
  } | null>(null);

  useEffect(() => {
    void (async () => {
      const [dash, rev] = await Promise.all([
        api.getDashboardOverview(),
        api.getRevenueLeakSummary(),
      ]);
      setOverview(dash.data);
      setSource(dash.source);
      setLeak(rev.data);
    })();
  }, []);

  if (!overview) {
    return (
      <AppShell>
        <div className="animate-pulse-soft text-slate-400">Đang tải dashboard…</div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Tổng quan chất lượng telesale"
        description="Điểm nhân viên, KPI vận hành và ước tính rò rỉ doanh thu trong cửa sổ đã chọn."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {overview.kpis.map((k) => (
          <KpiCard
            key={k.key}
            label={k.label}
            value={
              k.unit === "%"
                ? formatPercentDisplay(k.value)
                : k.unit === "điểm"
                  ? formatNumber(k.value, 1)
                  : formatNumber(k.value)
            }
            delta={k.delta_pct}
            hint={k.unit === "case" ? "case mở" : undefined}
          />
        ))}
      </div>

      <div className="mb-6 grid gap-4 xl:grid-cols-3">
        <Panel title="Tóm tắt cửa sổ" className="animate-fade-up">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-slate-500">Tổng cuộc gọi</dt>
              <dd className="mt-1 text-lg font-semibold text-slate-100">
                {formatNumber(overview.calls_total)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Đã chấm</dt>
              <dd className="mt-1 text-lg font-semibold text-slate-100">
                {formatNumber(overview.scored)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Fail</dt>
              <dd className="mt-1 text-lg font-semibold text-rose-300">
                {formatNumber(overview.failed)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Thiếu bằng chứng</dt>
              <dd className="mt-1 text-lg font-semibold text-amber-300">
                {formatNumber(overview.insufficient_evidence)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Điểm TB</dt>
              <dd className={`mt-1 text-lg font-semibold ${scoreTone(overview.avg_score)}`}>
                {formatNumber(overview.avg_score, 1)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Auto-fail rate</dt>
              <dd className="mt-1 text-lg font-semibold text-slate-100">
                {formatPercentDisplay(overview.auto_fail_rate)}
              </dd>
            </div>
          </dl>
        </Panel>

        <Panel title="Rò rỉ doanh thu (org)" className="xl:col-span-2 animate-fade-up">
          {leak ? <RevenueLeakChart rollup={leak} /> : null}
          <div className="mt-3 text-xs text-slate-500">
            Ước tính org: {formatVnd(overview.estimated_revenue_leak_vnd)} · IE không cộng tiền:{" "}
            {overview.ie_leak_calls} cuộc
          </div>
        </Panel>
      </div>

      <div className="mb-6 grid gap-4 xl:grid-cols-2">
        <Panel title="Bảng điểm nhân viên">
          <DataTable
            rows={overview.employee_scores}
            rowKey={(r) => r.user_id}
            columns={[
              {
                key: "name",
                header: "Nhân viên",
                render: (r) => (
                  <div>
                    <div className="font-medium">{r.full_name}</div>
                    <div className="text-xs text-slate-500">{r.team_name}</div>
                  </div>
                ),
              },
              {
                key: "score",
                header: "Điểm TB",
                render: (r) => (
                  <span className={scoreTone(r.avg_score)}>
                    {formatNumber(r.avg_score, 1)}
                  </span>
                ),
              },
              {
                key: "calls",
                header: "Cuộc gọi",
                render: (r) => formatNumber(r.calls),
              },
              {
                key: "leak",
                header: "Leak",
                render: (r) => formatVnd(r.leak_vnd),
              },
              {
                key: "ie",
                header: "IE rate",
                render: (r) => formatPercentDisplay(r.ie_rate),
              },
            ]}
          />
        </Panel>

        <Panel>
          <StageHeatmap rows={overview.stage_heatmap} />
        </Panel>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Rule fail nhiều nhất">
          <ul className="space-y-2 text-sm">
            {overview.top_failed_rules.map((r) => (
              <li
                key={r.rule_code}
                className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 px-3 py-2"
              >
                <div>
                  <div className="font-mono text-xs text-teal-300">{r.rule_code}</div>
                  <div className="text-slate-200">{r.title}</div>
                </div>
                <div className="text-rose-300">{formatNumber(r.fail_count)}</div>
              </li>
            ))}
          </ul>
          <Link href="/rules" className="mt-3 inline-block text-xs text-teal-300 hover:underline">
            Mở rulebook →
          </Link>
        </Panel>
        <Panel title="Nguyên nhân gốc (Pareto)">
          <ul className="space-y-2 text-sm">
            {overview.top_root_causes.map((r) => (
              <li
                key={r.cause_code}
                className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 px-3 py-2"
              >
                <div>
                  <div className="font-mono text-xs text-teal-300">{r.cause_code}</div>
                  <div className="text-slate-200">{r.label}</div>
                </div>
                <div className="text-slate-300">{formatNumber(r.count)}</div>
              </li>
            ))}
          </ul>
          <Link href="/calls" className="mt-3 inline-block text-xs text-teal-300 hover:underline">
            Xem danh sách cuộc gọi →
          </Link>
        </Panel>
      </div>
    </AppShell>
  );
}
