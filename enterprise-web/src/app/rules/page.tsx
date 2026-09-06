"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { DataTable } from "@/components/DataTable";
import { Badge, PageHeader, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import { SEVERITY_LABELS, STAGE_LABELS } from "@/lib/format";
import type { RulebookRule } from "@/lib/types";

export default function RulesPage() {
  const [rows, setRows] = useState<RulebookRule[]>([]);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<RulebookRule | null>(null);

  useEffect(() => {
    const handle = setTimeout(() => {
      void (async () => {
        const res = await api.listRules({
          category: category || undefined,
          q: q || undefined,
        });
        setRows(res.data.data);
        setSource(res.source);
        setSelected((prev) => prev ?? res.data.data[0] ?? null);
      })();
    }, 200);
    return () => clearTimeout(handle);
  }, [category, q]);

  return (
    <AppShell>
      <PageHeader
        title="Rulebook"
        description="Duyệt tiêu chí chấm điểm từ API rulebook (immutable theo release)."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex flex-wrap gap-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Tìm rule_code hoặc tiêu đề…"
          className="min-w-[240px] flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none ring-teal-500/30 focus:ring-2"
        />
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
        >
          <option value="">Tất cả category</option>
          <option value="opening">opening · {STAGE_LABELS.opening}</option>
          <option value="discovery">discovery · {STAGE_LABELS.discovery}</option>
          <option value="objection">objection · {STAGE_LABELS.objection}</option>
          <option value="close">close · {STAGE_LABELS.close}</option>
          <option value="compliance">compliance</option>
        </select>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <DataTable
          rows={rows}
          rowKey={(r) => r.rule_code}
          onRowClick={setSelected}
          emptyText="Không tìm thấy rule."
          columns={[
            {
              key: "code",
              header: "Rule",
              render: (r) => (
                <span className="font-mono text-xs text-teal-300">
                  {r.rule_code}
                </span>
              ),
            },
            {
              key: "title",
              header: "Tiêu đề",
              render: (r) => r.title,
            },
            {
              key: "cat",
              header: "Category",
              render: (r) => <Badge>{r.category}</Badge>,
            },
            {
              key: "sev",
              header: "Mức",
              render: (r) => (
                <Badge
                  tone={
                    r.severity === "critical"
                      ? "rose"
                      : r.severity === "major"
                        ? "amber"
                        : "slate"
                  }
                >
                  {SEVERITY_LABELS[r.severity] || r.severity}
                </Badge>
              ),
            },
            {
              key: "weight",
              header: "Weight",
              render: (r) => r.weight.toFixed(2),
            },
            {
              key: "af",
              header: "Auto-fail",
              render: (r) =>
                r.auto_fail ? <Badge tone="rose">Có</Badge> : <Badge>Không</Badge>,
            },
          ]}
        />

        <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
          {selected ? (
            <div className="space-y-3 text-sm">
              <div className="font-mono text-xs text-teal-300">
                {selected.rule_code} · v{selected.version}
              </div>
              <h2 className="text-lg font-semibold text-slate-50">
                {selected.title}
              </h2>
              <p className="leading-relaxed text-slate-300">
                {selected.description}
              </p>
              <dl className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <dt className="text-slate-500">Evaluator</dt>
                  <dd className="mt-1 font-mono text-slate-200">
                    {selected.evaluator_type}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Status</dt>
                  <dd className="mt-1">
                    <Badge
                      tone={selected.status === "active" ? "emerald" : "amber"}
                    >
                      {selected.status}
                    </Badge>
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Severity</dt>
                  <dd className="mt-1 text-slate-200">
                    {SEVERITY_LABELS[selected.severity]}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">Weight</dt>
                  <dd className="mt-1 text-slate-200">{selected.weight}</dd>
                </div>
              </dl>
              <p className="text-xs text-slate-500">
                UI chỉ đọc rule từ API/DB — không hardcode business threshold.
              </p>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Chọn một rule để xem chi tiết.</p>
          )}
        </section>
      </div>
    </AppShell>
  );
}
