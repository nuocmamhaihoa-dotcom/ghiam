"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

type ProposalItem = {
  proposal_id: string;
  kind?: string;
  title?: string;
  summary?: string;
  confidence?: number;
  novelty?: number;
  evidence_count?: number;
  quality_score?: number;
  status?: string;
  suggested_rule?: string;
  suggested_coaching?: string;
};

export default function QaApprovalPage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [items, setItems] = useState<ProposalItem[]>([]);
  const [selected, setSelected] = useState<ProposalItem | null>(null);
  const [editRule, setEditRule] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadQueue() {
    setError(null);
    const res = await api.selfLearningQaQueue();
    const data = res.data as { items?: ProposalItem[] };
    setItems(data.items || []);
    setSource(res.source);
    setSelected(null);
  }

  async function approve(id: string) {
    setError(null);
    const res = await api.selfLearningApprove(id, "qa_ui");
    setResult(res.data);
    setSource(res.source);
    await loadQueue();
  }

  async function reject(id: string) {
    setError(null);
    const res = await api.selfLearningReject(id, "qa_ui", "Not enough evidence");
    setResult(res.data);
    setSource(res.source);
    await loadQueue();
  }

  async function promote(id: string) {
    setError(null);
    const res = await api.selfLearningPromote(id, "qa_ui");
    setResult(res.data);
    setSource(res.source);
  }

  async function edit(id: string) {
    setError(null);
    const res = await api.selfLearningEdit(
      id,
      { suggested_rule: editRule || selected?.suggested_rule },
      "qa_ui"
    );
    setResult(res.data);
    setSource(res.source);
    await loadQueue();
  }

  return (
    <AppShell>
      <PageHeader
        title="QA Approval Center"
        description="Duyệt Rule / Intent / Objection / Coaching mới. Chỉ Approve mới được đưa vào Production."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex gap-2">
        <button
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          onClick={loadQueue}
        >
          Refresh QA Queue
        </button>
      </div>

      {error ? (
        <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <Panel title={`Pending QA (${items.length})`}>
          <div className="space-y-3">
            {items.map((item) => (
              <button
                key={item.proposal_id}
                className="block w-full rounded-lg border border-slate-800 bg-slate-950/40 p-3 text-left hover:bg-slate-900/60"
                onClick={() => {
                  setSelected(item);
                  setEditRule(item.suggested_rule || "");
                }}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-medium text-slate-100">{item.title}</p>
                    <p className="mt-1 text-xs text-slate-400">{item.summary}</p>
                  </div>
                  <span className="text-xs uppercase tracking-wide text-slate-500">
                    {item.kind}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
                  <span>conf {item.confidence ?? "-"}</span>
                  <span>novelty {item.novelty ?? "-"}</span>
                  <span>evidence {item.evidence_count ?? "-"}</span>
                  <span>quality {item.quality_score ?? "-"}</span>
                </div>
              </button>
            ))}
            {!items.length && (
              <p className="text-sm text-slate-500">
                Queue trống — bấm Refresh hoặc ingest calls trước.
              </p>
            )}
          </div>
        </Panel>

        <Panel title="Review Actions">
          {selected ? (
            <div className="space-y-3 text-sm text-slate-300">
              <p className="font-medium text-slate-100">{selected.title}</p>
              <p>{selected.summary}</p>
              <label className="block text-xs text-slate-500">
                Edit suggested rule
                <textarea
                  className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 text-sm text-slate-100"
                  rows={3}
                  value={editRule}
                  onChange={(e) => setEditRule(e.target.value)}
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <button
                  className="rounded-lg border border-emerald-700/50 bg-emerald-900/30 px-3 py-2 text-sm text-emerald-100"
                  onClick={() => approve(selected.proposal_id)}
                >
                  Approve
                </button>
                <button
                  className="rounded-lg border border-rose-700/50 bg-rose-900/30 px-3 py-2 text-sm text-rose-100"
                  onClick={() => reject(selected.proposal_id)}
                >
                  Reject
                </button>
                <button
                  className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
                  onClick={() => edit(selected.proposal_id)}
                >
                  Edit
                </button>
                <button
                  className="rounded-lg border border-amber-700/50 bg-amber-900/30 px-3 py-2 text-sm text-amber-100"
                  onClick={() => promote(selected.proposal_id)}
                >
                  Promote to Production
                </button>
              </div>
              <p className="text-xs text-slate-500">
                Merge qua API `/merge`. Promote chỉ chạy khi đã Approve và vượt Quality Gate.
              </p>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Chọn một proposal từ queue.</p>
          )}
          {result && (
            <pre className="mt-4 max-h-64 overflow-auto rounded bg-slate-950/70 p-2 text-xs text-slate-300 whitespace-pre-wrap">
              {JSON.stringify(result, null, 2)}
            </pre>
          )}
        </Panel>
      </div>
    </AppShell>
  );
}
