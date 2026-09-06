"use client";

import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

const SAMPLE = [
  { speaker: "agent", text: "Em chào anh, bên em có gói ưu đãi tháng này." },
  { speaker: "customer", text: "Để em coi đã." },
  { speaker: "agent", text: "Dạ anh cần em giải thích thêm về bảo hành không ạ?" },
  { speaker: "customer", text: "Ừ cũng được." },
  { speaker: "agent", text: "Anh chốt luôn hôm nay để nhận quà tặng nhé." },
  { speaker: "customer", text: "Để hỏi vợ đã." },
  { speaker: "agent", text: "Dạ em xin số để gọi lại khi hai vợ chồng rảnh." },
  { speaker: "customer", text: "Mai gọi lại nhé." },
];

type TurnRow = {
  turn_index?: number;
  text?: string;
  intent_probability?: Record<string, number>;
  emotion_probability?: Record<string, number>;
  hidden_meaning?: string[];
  buying_probability?: number | null;
  exit_risk?: number | null;
  confidence?: number;
  evidence_quote?: string | null;
  status?: string;
};

function topKey(dist?: Record<string, number> | null): string {
  if (!dist || !Object.keys(dist).length) return "—";
  return Object.entries(dist).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "—";
}

export default function PragmaticsPage() {
  const [turnsJson, setTurnsJson] = useState(JSON.stringify(SAMPLE, null, 2));
  const [dialect, setDialect] = useState("south");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [error, setError] = useState<string | null>(null);

  const turns = (result?.turns as TurnRow[] | undefined) ?? [];
  const timeline = (result?.timeline as Record<string, unknown>[] | undefined) ?? [];
  const intentEvolution =
    (result?.intent_evolution as Record<string, unknown>[] | undefined) ?? [];
  const emotionEvolution =
    (result?.emotion_evolution as Record<string, unknown>[] | undefined) ?? [];

  const intentSeries = useMemo(
    () =>
      intentEvolution.map(
        (row) => `${row.turn_index}: ${row.intent} (${row.probability})`
      ),
    [intentEvolution]
  );
  const emotionSeries = useMemo(
    () =>
      emotionEvolution.map(
        (row) => `${row.turn_index}: ${row.emotion} (${row.probability})`
      ),
    [emotionEvolution]
  );

  async function run() {
    setError(null);
    try {
      const parsed = JSON.parse(turnsJson) as Record<string, unknown>[];
      const res = await api.analyzePragmatics(parsed, dialect || undefined);
      setResult(res.data);
      setSource(res.source);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analyze failed");
    }
  }

  return (
    <AppShell>
      <PageHeader
        title="Vietnamese Pragmatics Engine"
        description="Timeline ngữ dụng, hidden meaning, intent & emotion evolution — không kết luận theo keyword."
        actions={<SourcePill source={source} />}
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Transcript turns">
          <label className="mb-2 block text-xs text-slate-400">Dialect hint</label>
          <select
            className="mb-3 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={dialect}
            onChange={(e) => setDialect(e.target.value)}
          >
            <option value="north">north</option>
            <option value="central">central</option>
            <option value="south">south</option>
            <option value="">auto</option>
          </select>
          <textarea
            className="h-64 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 font-mono text-xs"
            value={turnsJson}
            onChange={(e) => setTurnsJson(e.target.value)}
          />
          <button
            type="button"
            onClick={() => void run()}
            className="mt-3 rounded-lg bg-teal-500/90 px-4 py-2 text-sm font-medium text-slate-950"
          >
            Phân tích ngữ dụng
          </button>
          {error ? <p className="mt-2 text-sm text-rose-400">{error}</p> : null}
        </Panel>

        <Panel title="Summary">
          <pre className="overflow-auto rounded-lg bg-slate-950/80 p-3 text-xs text-slate-300">
            {result
              ? JSON.stringify(
                  {
                    status: result.status,
                    dialect: result.dialect,
                    summary: result.summary,
                    explanation: result.explanation,
                    intents: result.intents,
                    objections: result.objections,
                  },
                  null,
                  2
                )
              : "Chưa chạy."}
          </pre>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Pragmatics Timeline">
          <div className="space-y-3">
            {(timeline.length ? timeline : turns).map((row, idx) => {
              const item = row as Record<string, unknown>;
              const turnIndex = Number(item.turn_index ?? turns[idx]?.turn_index ?? idx);
              const text = String(item.text ?? turns[idx]?.text ?? "");
              const topIntent = String(
                item.top_intent ?? topKey(turns[idx]?.intent_probability)
              );
              const hidden =
                (item.hidden_meaning as string[] | undefined) ??
                turns[idx]?.hidden_meaning ??
                [];
              return (
                <div key={`${turnIndex}-${idx}`} className="border-l-2 border-teal-500/40 pl-3">
                  <div className="text-[11px] uppercase tracking-wide text-slate-500">
                    Turn {turnIndex} · {topIntent}
                  </div>
                  <div className="text-sm text-slate-200">{text}</div>
                  {hidden.length ? (
                    <div className="mt-1 text-xs text-amber-300/90">Hidden: {hidden[0]}</div>
                  ) : null}
                </div>
              );
            })}
            {!timeline.length && !turns.length ? (
              <div className="text-sm text-slate-500">Chưa có timeline.</div>
            ) : null}
          </div>
        </Panel>

        <Panel title="Hidden Meaning">
          <ul className="space-y-2 text-sm text-slate-300">
            {turns.flatMap((t) =>
              (t.hidden_meaning ?? []).map((h, i) => (
                <li
                  key={`${t.turn_index ?? 0}-${i}`}
                  className="rounded-md bg-slate-950/70 px-3 py-2"
                >
                  <span className="text-slate-500">#{t.turn_index}</span> {h}
                  {t.evidence_quote ? (
                    <div className="mt-1 text-xs text-slate-500">“{t.evidence_quote}”</div>
                  ) : null}
                </li>
              ))
            )}
            {!turns.some((t) => (t.hidden_meaning ?? []).length) ? (
              <li className="text-slate-500">Chưa có hidden meaning.</li>
            ) : null}
          </ul>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Intent Evolution">
          <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-300">
            {intentSeries.map((line) => (
              <li key={line}>{line}</li>
            ))}
            {!intentSeries.length ? <li className="text-slate-500">Chưa có dữ liệu.</li> : null}
          </ol>
        </Panel>
        <Panel title="Emotion Evolution">
          <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-300">
            {emotionSeries.map((line) => (
              <li key={line}>{line}</li>
            ))}
            {!emotionSeries.length ? <li className="text-slate-500">Chưa có dữ liệu.</li> : null}
          </ol>
        </Panel>
      </div>
    </AppShell>
  );
}
