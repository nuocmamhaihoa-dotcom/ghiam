"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { Badge, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import { VERDICT_LABELS, formatDateTime } from "@/lib/format";
import type { Appeal, Verdict } from "@/lib/types";

const STATUS_LABEL: Record<Appeal["status"], string> = {
  open: "Mở",
  under_review: "Đang xét",
  overturned: "Đảo verdict",
  upheld: "Giữ nguyên",
};

export default function AppealsPage() {
  const [rows, setRows] = useState<Appeal[]>([]);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [decisionNote, setDecisionNote] = useState("");
  const [localStatus, setLocalStatus] = useState<
    Record<string, Appeal["status"]>
  >({});

  useEffect(() => {
    void (async () => {
      const res = await api.listAppeals();
      setRows(res.data);
      setSource(res.source);
      setActiveId(res.data.find((a) => a.status !== "overturned")?.id ?? res.data[0]?.id ?? null);
    })();
  }, []);

  const active = useMemo(() => {
    const base = rows.find((r) => r.id === activeId);
    if (!base) return null;
    return {
      ...base,
      status: localStatus[base.id] || base.status,
    };
  }, [activeId, localStatus, rows]);

  function decide(next: "overturned" | "upheld") {
    if (!active) return;
    if (!decisionNote.trim()) return;
    setLocalStatus((s) => ({ ...s, [active.id]: next }));
  }

  return (
    <AppShell>
      <PageHeader
        title="Appeal Mode"
        description="Đối chiếu verdict AI vs đề xuất human; bắt buộc note + evidence trước khi đảo điểm."
        actions={<SourcePill source={source} />}
      />

      <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
        <DataTable
          rows={rows.map((r) => ({
            ...r,
            status: localStatus[r.id] || r.status,
          }))}
          rowKey={(r) => r.id}
          onRowClick={(r) => {
            setActiveId(r.id);
            setDecisionNote("");
          }}
          columns={[
            {
              key: "id",
              header: "Appeal",
              render: (r) => (
                <span className="font-mono text-xs text-teal-300">{r.id}</span>
              ),
            },
            {
              key: "call",
              header: "Call / Agent",
              render: (r) => (
                <div>
                  <Link
                    href={`/calls/${r.call_id}`}
                    className="font-mono text-xs text-sky-300 hover:underline"
                  >
                    {r.call_id}
                  </Link>
                  <div className="text-sm">{r.agent_name}</div>
                </div>
              ),
            },
            {
              key: "rule",
              header: "Rule",
              render: (r) => (
                <div>
                  <div className="font-mono text-xs">{r.rule_code}</div>
                  <div className="text-xs text-slate-500">{r.rule_title}</div>
                </div>
              ),
            },
            {
              key: "status",
              header: "Trạng thái",
              render: (r) => (
                <Badge
                  tone={
                    r.status === "overturned"
                      ? "emerald"
                      : r.status === "upheld"
                        ? "rose"
                        : r.status === "under_review"
                          ? "sky"
                          : "amber"
                  }
                >
                  {STATUS_LABEL[r.status]}
                </Badge>
              ),
            },
          ]}
        />

        <Panel title="Chi tiết appeal">
          {!active ? (
            <p className="text-sm text-slate-500">Chọn một appeal.</p>
          ) : (
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <VerdictCard
                  title="Verdict hiện tại (AI)"
                  verdict={active.current_verdict}
                />
                <VerdictCard
                  title="Đề xuất human"
                  verdict={active.proposed_verdict}
                />
              </div>

              <div>
                <div className="text-xs text-slate-500">Lý do</div>
                <div className="mt-1 font-mono text-xs text-teal-300">
                  {active.reason_code}
                </div>
                <p className="mt-1 text-slate-200">{active.reason_text}</p>
              </div>

              {active.evidence_quote ? (
                <blockquote className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2 italic text-slate-300">
                  “{active.evidence_quote}”
                </blockquote>
              ) : (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-amber-100">
                  Chưa đính kèm evidence quote — bắt buộc trước khi overturn.
                </div>
              )}

              <div className="text-xs text-slate-500">
                Tạo lúc {formatDateTime(active.created_at)} · status{" "}
                {STATUS_LABEL[active.status]}
              </div>

              {active.status === "open" || active.status === "under_review" ? (
                <div className="space-y-2 border-t border-slate-800 pt-4">
                  <label className="block">
                    <span className="mb-1.5 block text-xs text-slate-400">
                      Ghi chú quyết định (bắt buộc)
                    </span>
                    <textarea
                      value={decisionNote}
                      onChange={(e) => setDecisionNote(e.target.value)}
                      rows={3}
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none ring-teal-500/30 focus:ring-2"
                      placeholder="Verified after replay…"
                    />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={!decisionNote.trim() || !active.evidence_quote}
                      onClick={() => decide("overturned")}
                      className="rounded-lg bg-emerald-500/90 px-3 py-2 text-xs font-semibold text-slate-950 disabled:opacity-40"
                    >
                      Overturn → pass/proposed
                    </button>
                    <button
                      type="button"
                      disabled={!decisionNote.trim()}
                      onClick={() => decide("upheld")}
                      className="rounded-lg border border-rose-500/40 px-3 py-2 text-xs font-semibold text-rose-200 disabled:opacity-40"
                    >
                      Uphold AI verdict
                    </button>
                  </div>
                </div>
              ) : (
                <Badge tone="slate">Đã đóng — audit sẽ ghi actor + note</Badge>
              )}
            </div>
          )}
        </Panel>
      </div>
    </AppShell>
  );
}

function VerdictCard({ title, verdict }: { title: string; verdict: Verdict }) {
  const tone =
    verdict === "pass"
      ? "emerald"
      : verdict === "fail"
        ? "rose"
        : verdict === "Insufficient Evidence"
          ? "amber"
          : "sky";
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
      <div className="text-xs text-slate-500">{title}</div>
      <div className="mt-2">
        <Badge tone={tone}>{VERDICT_LABELS[verdict] || verdict}</Badge>
      </div>
    </div>
  );
}
