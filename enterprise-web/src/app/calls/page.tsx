"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { Badge, PageHeader, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import {
  VERDICT_LABELS,
  formatDateTime,
  formatDuration,
  formatNumber,
  scoreTone,
} from "@/lib/format";
import type { CallSummary } from "@/lib/types";

export default function CallsPage() {
  const router = useRouter();
  const [rows, setRows] = useState<CallSummary[]>([]);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    void (async () => {
      const res = await api.listCalls({
        status: status || undefined,
        limit: 50,
      });
      setRows(res.data.data);
      setSource(res.source);
    })();
  }, [status]);

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return rows;
    return rows.filter(
      (r) =>
        r.agent_name.toLowerCase().includes(term) ||
        r.external_call_id.toLowerCase().includes(term) ||
        r.campaign_code.toLowerCase().includes(term) ||
        r.id.toLowerCase().includes(term)
    );
  }, [q, rows]);

  return (
    <AppShell>
      <PageHeader
        title="Danh sách cuộc gọi"
        description="Lọc theo trạng thái pipeline và mở chi tiết scorecard / evidence."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex flex-wrap gap-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Tìm agent, campaign, call id…"
          className="min-w-[240px] flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none ring-teal-500/30 focus:ring-2"
        />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
        >
          <option value="">Tất cả trạng thái</option>
          <option value="scored">Đã chấm</option>
          <option value="needs_review">Cần review</option>
          <option value="processing">Đang xử lý</option>
          <option value="failed">Lỗi</option>
        </select>
      </div>

      <DataTable
        rows={filtered}
        rowKey={(r) => r.id}
        onRowClick={(r) => router.push(`/calls/${r.id}`)}
        emptyText="Không có cuộc gọi phù hợp bộ lọc."
        columns={[
          {
            key: "id",
            header: "Call",
            render: (r) => (
              <div>
                <div className="font-mono text-xs text-teal-300">{r.id}</div>
                <div className="text-xs text-slate-500">{r.external_call_id}</div>
              </div>
            ),
          },
          {
            key: "agent",
            header: "Nhân viên",
            render: (r) => r.agent_name,
          },
          {
            key: "campaign",
            header: "Campaign",
            render: (r) => (
              <span className="font-mono text-xs">{r.campaign_code}</span>
            ),
          },
          {
            key: "when",
            header: "Thời gian",
            render: (r) => (
              <div>
                <div>{formatDateTime(r.started_at)}</div>
                <div className="text-xs text-slate-500">
                  {formatDuration(r.duration_sec)}
                </div>
              </div>
            ),
          },
          {
            key: "score",
            header: "Điểm",
            render: (r) =>
              r.overall_score == null ? (
                <Badge tone="amber">IE</Badge>
              ) : (
                <span className={scoreTone(r.overall_score)}>
                  {formatNumber(r.overall_score, 1)}
                </span>
              ),
          },
          {
            key: "result",
            header: "Kết quả",
            render: (r) => {
              if (!r.result) return <Badge>Đang chạy</Badge>;
              const tone =
                r.result === "pass"
                  ? "emerald"
                  : r.result === "fail"
                    ? "rose"
                    : r.result === "Insufficient Evidence"
                      ? "amber"
                      : "sky";
              return (
                <Badge tone={tone}>
                  {VERDICT_LABELS[r.result] || r.result}
                </Badge>
              );
            },
          },
          {
            key: "crm",
            header: "CRM",
            render: (r) => r.crm_outcome || "—",
          },
          {
            key: "phone",
            header: "SĐT",
            render: (r) => (
              <span className="font-mono text-xs text-slate-400">
                {r.customer_phone_masked}
              </span>
            ),
          },
        ]}
      />
    </AppShell>
  );
}
