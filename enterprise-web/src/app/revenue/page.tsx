"use client";

import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ParetoChart } from "@/components/ParetoChart";
import { RevenueLeakChart } from "@/components/RevenueLeakChart";
import { Badge, KpiCard, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber, formatVnd } from "@/lib/format";

type RevenueRollup = {
  estimated_total: number;
  currency: string;
  insufficient_evidence_calls: number;
  top_components: {
    cause_code: string;
    amount: number;
    call_count: number;
  }[];
  recoverable_estimate?: number;
};

export default function RevenuePage() {
  const [leak, setLeak] = useState<RevenueRollup | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await api.getRevenueLeakSummary();
        if (cancelled) return;
        setLeak(res.data as RevenueRollup);
        setSource(res.source);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Không tải được revenue leak"
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const paretoItems = useMemo(() => {
    const rows = leak?.top_components ?? [];
    const total = rows.reduce((sum, row) => sum + row.amount, 0) || 1;
    let cumulative = 0;
    return rows.map((row) => {
      cumulative += row.amount;
      return {
        cause_code: row.cause_code,
        label: row.cause_code,
        count: row.call_count,
        share: row.amount / total,
        cumulative_share: cumulative / total,
      };
    });
  }, [leak]);

  return (
    <AppShell>
      <PageHeader
        title="Revenue Leak AI"
        description="Top leak · Leader gap · Product bottleneck · Recoverable revenue"
        actions={<SourcePill source={source} />}
      />

      {error ? (
        <Panel>
          <p className="text-sm text-rose-300">{error}</p>
        </Panel>
      ) : null}

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Estimated leak"
          value={formatVnd(leak?.estimated_total ?? 0)}
          hint={leak?.currency || "VND"}
        />
        <KpiCard
          label="Recoverable"
          value={formatVnd(
            leak?.recoverable_estimate ??
              Math.round((leak?.estimated_total ?? 0) * 0.45)
          )}
          hint="Ước tính có thể cứu"
        />
        <KpiCard
          label="IE calls"
          value={formatNumber(leak?.insufficient_evidence_calls ?? 0)}
          hint="Insufficient Evidence — không quy leak"
        />
        <KpiCard
          label="Leak drivers"
          value={formatNumber(leak?.top_components?.length ?? 0)}
          hint="Root-cause components"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Leak composition">
          <RevenueLeakChart rollup={leak ?? undefined} />
        </Panel>
        <Panel title="Pareto · Top revenue leak">
          {paretoItems.length ? (
            <ParetoChart items={paretoItems} title="Pareto leak drivers" />
          ) : (
            <p className="text-sm text-slate-500">
              Chưa có component leak đủ evidence.
            </p>
          )}
        </Panel>
      </div>

      <div className="mt-4">
        <Panel title="Driver detail">
          <div className="space-y-2">
            {(leak?.top_components ?? []).map((c) => (
              <div
                key={c.cause_code}
                className="flex items-center justify-between rounded-lg border border-slate-800 px-3 py-2"
              >
                <div>
                  <div className="text-sm text-slate-100">{c.cause_code}</div>
                  <div className="text-xs text-slate-500">
                    {c.call_count} cuộc gọi
                  </div>
                </div>
                <Badge tone="rose">{formatVnd(c.amount)}</Badge>
              </div>
            ))}
            {!leak?.top_components?.length ? (
              <p className="text-sm text-slate-500">
                Insufficient Evidence hoặc chưa có FAIL gắn revenue impact.
              </p>
            ) : null}
          </div>
        </Panel>
      </div>
    </AppShell>
  );
}
