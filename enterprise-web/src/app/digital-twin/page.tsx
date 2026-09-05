"use client";

import { useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

const DEMO_CALLS = [
  {
    call_id: "dt-demo-1",
    label: "golden",
    qa_score: 92,
    turns: [
      { speaker: "agent", text: "Dạ anh/chị đang quan tâm sản phẩm nào ạ? Em muốn hiểu nhu cầu trước." },
      { speaker: "customer", text: "Giá hơi cao." },
      { speaker: "agent", text: "Em hiểu anh/chị đang cân nhắc. Lợi ích chính là tiết kiệm dài hạn." },
      { speaker: "agent", text: "Nếu ổn, mình xác nhận và chốt luôn ạ." },
    ],
  },
  {
    call_id: "dt-demo-2",
    label: "qa_approved",
    qa_score: 88,
    turns: [
      { speaker: "agent", text: "Anh/chị tiện nói chuyện vài phút không ạ?" },
      { speaker: "customer", text: "Đang cân nhắc." },
      { speaker: "agent", text: "Em nắm ạ. Anh/chị ưu tiên tiêu chí nào nhất?" },
      { speaker: "agent", text: "Em đề xuất gói phù hợp và mình xác nhận bước tiếp theo nhé." },
    ],
  },
  {
    call_id: "dt-demo-3",
    label: "high_conversion",
    qa_score: 90,
    turns: [
      { speaker: "agent", text: "Em hỏi nhanh nhu cầu hiện tại của anh/chị được không ạ?" },
      { speaker: "customer", text: "Muốn tiết kiệm." },
      { speaker: "agent", text: "Em hiểu. Giá trị chính là tiết kiệm bền vững." },
      { speaker: "agent", text: "Nếu ổn, em gửi thông tin để anh/chị chốt ạ." },
    ],
  },
];

export default function DigitalTwinPage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null);
  const [trainResult, setTrainResult] = useState<Record<string, unknown> | null>(null);
  const [roleplay, setRoleplay] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);
  const [twinId, setTwinId] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function loadDashboard() {
    setError(null);
    const res = await api.digitalTwinDashboard();
    setDashboard(res.data);
    setSource(res.source);
  }

  async function trainDemoTwin() {
    setError(null);
    const res = await api.digitalTwinTrain({
      agent_id: "demo-agent",
      display_name: "Twin Demo Lan",
      calls: DEMO_CALLS,
      activate: true,
    });
    setTrainResult(res.data);
    setSource(res.source);
    const twin = (res.data.twin as Record<string, unknown> | undefined) || {};
    if (typeof twin.twin_id === "string") setTwinId(twin.twin_id);
    return typeof twin.twin_id === "string" ? twin.twin_id : "";
  }

  async function runRoleplay() {
    setError(null);
    let id = twinId;
    if (!id) id = await trainDemoTwin();
    if (!id) {
      setError("Chưa có Twin — hãy Train Demo Twin trước.");
      return;
    }
    const res = await api.digitalTwinRoleplay(id, {
      trainee_id: "trainee-demo",
      scenario: "price_objection",
      trainee_turns: [
        "Dạ em hiểu anh đang lo về giá. Anh ưu tiên điều gì nhất ạ?",
        "Lợi ích dài hạn sẽ tiết kiệm hơn. Anh xem mình chốt gói phù hợp nhé?",
      ],
    });
    setRoleplay(res.data);
    setSource(res.source);
  }

  async function loadQuality() {
    setError(null);
    const res = await api.digitalTwinQuality();
    setQuality(res.data);
    setSource(res.source);
  }

  const widgets = (dashboard?.widgets as Record<string, unknown> | undefined) || {};
  const session = (roleplay?.session as Record<string, unknown> | undefined) || {};
  const topDiff =
    (dashboard?.top_differences as Array<Record<string, unknown>> | undefined) || [];

  return (
    <AppShell>
      <PageHeader
        title="AI Digital Twin Salesperson"
        description="Bản sao AI của telesale xuất sắc — học từ Golden/QA/High-conversion, không sao chép nguyên văn."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <button
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          onClick={loadDashboard}
        >
          Load Dashboard
        </button>
        <button
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          onClick={() => void trainDemoTwin()}
        >
          Train Demo Twin
        </button>
        <button
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          onClick={() => void runRoleplay()}
        >
          Run Roleplay
        </button>
        <button
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          onClick={loadQuality}
        >
          Quality Gate
        </button>
      </div>

      {error ? (
        <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Object.entries(widgets).map(([key, value]) => (
          <Panel key={key} title={key.replaceAll("_", " ")}>
            <p className="text-2xl font-semibold tabular-nums text-slate-50">{String(value)}</p>
          </Panel>
        ))}
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Panel title="Twin Similarity / Skill Gap / Progress">
          <ul className="space-y-2 text-sm text-slate-300">
            <li className="flex justify-between border-b border-slate-800 pb-1">
              <span>Twin Similarity</span>
              <span className="font-medium text-slate-100">{String(widgets.avg_similarity ?? "—")}</span>
            </li>
            <li className="flex justify-between border-b border-slate-800 pb-1">
              <span>Skill Gap Index</span>
              <span className="font-medium text-slate-100">{String(widgets.skill_gap_index ?? "—")}</span>
            </li>
            <li className="flex justify-between border-b border-slate-800 pb-1">
              <span>Progress</span>
              <span className="font-medium text-slate-100">{String(widgets.progress ?? "—")}</span>
            </li>
          </ul>
          <p className="mt-3 text-xs text-slate-500">Top Difference</p>
          <ul className="mt-1 space-y-1 text-sm text-slate-300">
            {topDiff.length ? (
              topDiff.map((d, i) => (
                <li key={i}>
                  {String(d.skill)} — gap {String(d.gap)}
                </li>
              ))
            ) : (
              <li className="text-slate-500">Chưa có dữ liệu roleplay.</li>
            )}
          </ul>
        </Panel>

        <Panel title="Roleplay Coaching">
          {session.similarity_score != null ? (
            <div className="space-y-2 text-sm text-slate-300">
              <p>
                Similarity Score:{" "}
                <strong className="text-slate-100">{String(session.similarity_score)}</strong>
              </p>
              <p>
                Improvement Score:{" "}
                <strong className="text-slate-100">{String(session.improvement_score)}</strong>
              </p>
              <ul className="list-disc pl-5">
                {((session.coaching as string[]) || []).map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Chưa chạy roleplay.</p>
          )}
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Panel title="Train Result">
          {trainResult ? (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs text-slate-300">
              {JSON.stringify(trainResult, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-slate-500">
              Chưa train — chỉ dùng golden / QA / high-conversion.
            </p>
          )}
        </Panel>
        <Panel title="Quality Gate">
          {quality ? (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs text-slate-300">
              {JSON.stringify(quality, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-slate-500">Chưa load quality snapshot.</p>
          )}
        </Panel>
      </div>
    </AppShell>
  );
}
