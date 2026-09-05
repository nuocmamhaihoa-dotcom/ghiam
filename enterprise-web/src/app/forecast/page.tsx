"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function ForecastPage() {
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");

  async function run() {
    const res = await api.forecastKpi(
      [
        { conversion_rate: 0.18, revenue: 90000000 },
        { conversion_rate: 0.2, revenue: 100000000 },
        { conversion_rate: 0.22, revenue: 110000000 },
        { conversion_rate: 0.21, revenue: 105000000 },
        { conversion_rate: 0.23, revenue: 120000000 },
        { conversion_rate: 0.24, revenue: 125000000 },
      ],
      30
    );
    setResult(res.data);
    setSource(res.source);
  }

  return (
    <AppShell>
      <PageHeader
        title="Sales Forecast"
        description="Dự báo conversion & revenue từ chuỗi KPI lịch sử + tín hiệu revenue leak."
        actions={<SourcePill source={source} />}
      />
      <Panel title="KPI forecast">
        <button type="button" onClick={() => void run()} className="rounded-lg bg-teal-500/90 px-4 py-2 text-sm text-slate-950">Forecast 30 ngày</button>
        <pre className="mt-3 overflow-auto rounded bg-slate-950/80 p-3 text-xs text-slate-300">{result ? JSON.stringify(result, null, 2) : "Chưa forecast."}</pre>
      </Panel>
    </AppShell>
  );
}
