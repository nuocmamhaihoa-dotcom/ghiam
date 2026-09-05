"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { Badge, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDateTime, formatNumber, scoreTone } from "@/lib/format";
import type { QaQueueItem } from "@/lib/types";

const QUEUE_LABEL: Record<QaQueueItem["queue"], string> = {
  calibration: "Calibration",
  low_confidence: "Low confidence",
  compliance: "Compliance risk",
  appeals: "Appeals",
  ie_heavy: "IE nặng",
};

export default function QaPage() {
  const [rows, setRows] = useState<QaQueueItem[]>([]);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [queue, setQueue] = useState<"" | QaQueueItem["queue"]>("");

  useEffect(() => {
    void (async () => {
      const res = await api.listQaQueue();
      setRows(res.data);
      setSource(res.source);
    })();
  }, []);

  const filtered = useMemo(
    () => (queue ? rows.filter((r) => r.queue === queue) : rows),
    [queue, rows]
  );

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const r of rows) map[r.queue] = (map[r.queue] || 0) + 1;
    return map;
  }, [rows]);

  return (
    <AppShell>
      <PageHeader
        title="QA Review & Calibration"
        description="Hàng đợi review: calibration, low confidence, compliance, IE nặng và appeals."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {(Object.keys(QUEUE_LABEL) as QaQueueItem["queue"][]).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setQueue(queue === key ? "" : key)}
            className={`rounded-xl border px-4 py-3 text-left transition ${
              queue === key
                ? "border-teal-500/50 bg-teal-500/10"
                : "border-slate-800 bg-slate-900/40 hover:border-slate-600"
            }`}
          >
            <div className="text-xs text-slate-500">{QUEUE_LABEL[key]}</div>
            <div className="mt-1 text-xl font-semibold text-slate-100">
              {counts[key] || 0}
            </div>
          </button>
        ))}
      </div>

      <Panel title="Hàng đợi hiện tại">
        <DataTable
          rows={filtered}
          rowKey={(r) => `${r.call_id}-${r.queue}`}
          emptyText="Hàng đợi trống."
          columns={[
            {
              key: "queue",
              header: "Queue",
              render: (r) => <Badge tone="sky">{QUEUE_LABEL[r.queue]}</Badge>,
            },
            {
              key: "call",
              header: "Call",
              render: (r) => (
                <Link
                  href={`/calls/${r.call_id}`}
                  className="font-mono text-xs text-teal-300 hover:underline"
                >
                  {r.call_id}
                </Link>
              ),
            },
            {
              key: "agent",
              header: "Nhân viên",
              render: (r) => r.agent_name,
            },
            {
              key: "score",
              header: "Điểm AI",
              render: (r) =>
                r.score == null ? (
                  <Badge tone="amber">IE</Badge>
                ) : (
                  <span className={scoreTone(r.score)}>
                    {formatNumber(r.score, 1)}
                  </span>
                ),
            },
            {
              key: "conf",
              header: "Confidence",
              render: (r) => `${(r.confidence * 100).toFixed(0)}%`,
            },
            {
              key: "reason",
              header: "Lý do flag",
              render: (r) => (
                <div>
                  <div className="text-slate-200">{r.reason}</div>
                  <div className="text-xs text-slate-500">
                    {formatDateTime(r.flagged_at)}
                  </div>
                </div>
              ),
            },
            {
              key: "actions",
              header: "Thao tác",
              render: (r) => (
                <div className="flex gap-2">
                  <Link
                    href={`/calls/${r.call_id}`}
                    className="rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800"
                  >
                    Review
                  </Link>
                  {r.queue === "calibration" ? (
                    <span className="rounded border border-teal-700/50 px-2 py-1 text-xs text-teal-300">
                      Gắn human label
                    </span>
                  ) : null}
                </div>
              ),
            },
          ]}
        />
      </Panel>

      <Panel title="Hướng dẫn calibration" className="mt-4">
        <ol className="list-decimal space-y-2 pl-5 text-sm text-slate-300">
          <li>Mở call → nghe audio + đối chiếu evidence spans.</li>
          <li>Không đổi rule logic trên UI; chỉ ghi human verdict + note.</li>
          <li>Insufficient Evidence phải giữ nguyên nếu thiếu span — không silent pass.</li>
          <li>Sau khi đủ mẫu, export delta cho sprint rulebook (Admin).</li>
        </ol>
      </Panel>
    </AppShell>
  );
}
