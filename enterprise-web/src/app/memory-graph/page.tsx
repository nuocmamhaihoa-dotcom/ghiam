"use client";

import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

type GraphNode = {
  id?: string;
  type?: string;
  label?: string;
  content?: string;
  source?: string;
  version?: string;
  confidence?: number;
};

type GraphEdge = {
  source?: string;
  target?: string;
  relation?: string;
};

type Citation = {
  id?: string;
  source?: string;
  node_type?: string;
  score?: number;
};

export default function MemoryGraphPage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [question, setQuestion] = useState("Phí thường niên thẻ tín dụng là bao nhiêu?");
  const [searchQ, setSearchQ] = useState("phản đối giá");
  const [entityId, setEntityId] = useState("pricing:the_tin_dung_base");
  const [explore, setExplore] = useState<Record<string, unknown> | null>(null);
  const [related, setRelated] = useState<Record<string, unknown> | null>(null);
  const [askResult, setAskResult] = useState<Record<string, unknown> | null>(null);
  const [history, setHistory] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const nodes = (explore?.nodes as GraphNode[] | undefined) ?? [];
  const edges = (explore?.edges as GraphEdge[] | undefined) ?? [];
  const citations = (askResult?.citations as Citation[] | undefined) ?? [];

  const typeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of nodes) {
      const t = n.type || "unknown";
      counts[t] = (counts[t] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [nodes]);

  async function loadExplore() {
    setError(null);
    const res = await api.exploreMemoryGraph(120);
    setExplore(res.data);
    setSource(res.source);
  }

  async function runSearch() {
    setError(null);
    const res = await api.searchKnowledge(searchQ);
    setRelated(res.data);
    setSource(res.source);
  }

  async function runAsk() {
    setError(null);
    const res = await api.askMemoryRag(question);
    setAskResult(res.data);
    setSource(res.source);
  }

  async function runHistory() {
    setError(null);
    const res = await api.memoryVersionHistory(entityId);
    setHistory(res.data);
    setSource(res.source);
  }

  async function runSync() {
    setError(null);
    const res = await api.syncMemoryKnowledge();
    setSource(res.source);
    await loadExplore();
  }

  async function runBuild() {
    setError(null);
    try {
      const res = await api.buildMemoryGraph({
        call_id: "demo-call-1",
        violations: [{ rule_code: "R-OBJ-01", message: "Cam kết lãi ngoài bảng" }],
        root_cause: { primary_code: "OVERPROMISE", confidence: 0.9 },
        coaching: { tips: [{ title: "Evidence close", tip: "Dẫn chứng bảng giá trước khi chốt" }] },
        intents: [{ name: "so_sanh_gia", confidence: 0.8 }],
        objections: [{ type: "price", text: "Đắt quá", confidence: 0.9 }],
        products: ["Thẻ tín dụng"],
      });
      setExplore({
        nodes: res.data.nodes,
        edges: res.data.edges,
        stats: res.data.stats,
        ok: true,
      });
      setSource(res.source);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Build failed");
    }
  }

  return (
    <AppShell>
      <PageHeader
        title="Enterprise Memory Graph + RAG"
        description="Bộ nhớ dài hạn: product ↔ SOP ↔ objection ↔ rulebook ↔ coaching. Trả lời chỉ khi có bằng chứng và citation."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <button type="button" onClick={() => void runSync()} className="rounded-lg bg-teal-500/90 px-3 py-2 text-sm text-slate-950">
          Sync knowledge
        </button>
        <button type="button" onClick={() => void loadExplore()} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200">
          Graph explorer
        </button>
        <button type="button" onClick={() => void runBuild()} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-200">
          Build from analysis
        </button>
      </div>
      {error ? <p className="mb-3 text-sm text-rose-400">{error}</p> : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Graph visualization">
          <div className="mb-3 flex flex-wrap gap-2 text-xs text-slate-400">
            {typeCounts.map(([type, count]) => (
              <span key={type} className="rounded-full border border-slate-700 px-2 py-1">
                {type}: {count}
              </span>
            ))}
          </div>
          <div className="max-h-72 space-y-2 overflow-auto">
            {nodes.slice(0, 40).map((n) => (
              <div key={n.id} className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="font-medium text-teal-300">{n.type}</span>
                  <span className="text-slate-500">v{n.version || "1.0.0"}</span>
                </div>
                <div className="mt-1 text-sm text-slate-100">{n.label || n.id}</div>
                <div className="mt-1 text-xs text-slate-400 line-clamp-2">{n.content}</div>
              </div>
            ))}
            {!nodes.length ? <p className="text-sm text-slate-500">Chưa tải graph. Bấm Sync hoặc Explorer.</p> : null}
          </div>
          <div className="mt-3 max-h-40 overflow-auto rounded-lg bg-slate-950/70 p-2 text-[11px] text-slate-400">
            {edges.slice(0, 30).map((e, idx) => (
              <div key={`${e.source}-${e.target}-${idx}`}>
                {e.source} —{e.relation}→ {e.target}
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Related knowledge">
          <div className="mb-2 flex gap-2">
            <input
              className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              placeholder="Tìm objection / SOP / pricing..."
            />
            <button type="button" onClick={() => void runSearch()} className="rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-900">
              Search
            </button>
          </div>
          <pre className="max-h-80 overflow-auto rounded-lg bg-slate-950/80 p-3 text-xs text-slate-300">
            {related ? JSON.stringify(related, null, 2) : "Chưa search."}
          </pre>
        </Panel>

        <Panel title="RAG answer + source citation">
          <textarea
            className="mb-2 h-24 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-sm"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <button type="button" onClick={() => void runAsk()} className="mb-3 rounded-lg bg-teal-500/90 px-3 py-2 text-sm text-slate-950">
            Ask (evidence-only)
          </button>
          <div className="mb-3 whitespace-pre-wrap rounded-lg bg-slate-950/80 p-3 text-sm text-slate-200">
            {String(askResult?.answer || "Chưa hỏi.")}
          </div>
          <div className="space-y-2">
            {citations.map((c) => (
              <div key={`${c.id}-${c.score}`} className="rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-300">
                <div className="font-medium text-teal-300">{c.id}</div>
                <div>
                  source: {c.source} · type: {c.node_type} · score: {c.score}
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Version history">
          <div className="mb-2 flex gap-2">
            <input
              className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              placeholder="node id"
            />
            <button type="button" onClick={() => void runHistory()} className="rounded-lg bg-slate-100 px-3 py-2 text-sm text-slate-900">
              Load
            </button>
          </div>
          <pre className="max-h-80 overflow-auto rounded-lg bg-slate-950/80 p-3 text-xs text-slate-300">
            {history ? JSON.stringify(history, null, 2) : "Chưa tải version history."}
          </pre>
        </Panel>
      </div>
    </AppShell>
  );
}
