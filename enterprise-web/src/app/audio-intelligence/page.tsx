"use client";

import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";

export default function AudioIntelligencePage() {
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null);
  const [processResult, setProcessResult] = useState<Record<string, unknown> | null>(null);
  const [quality, setQuality] = useState<Record<string, unknown> | null>(null);
  const [queueNote, setQueueNote] = useState("Drop audio/video/zip files — queue supports 1000+");

  async function loadDashboard() {
    const res = await api.audioIntelligenceDashboard();
    setDashboard(res.data);
    setSource(res.source);
  }

  async function runProcess() {
    const res = await api.audioIntelligenceProcess({
      file_meta: {
        file_id: "demo1",
        extension: ".mp3",
        integrity_ok: true,
        duration_sec: 45,
        sample_rate: 16000,
        channels: 1,
      },
      transcript_turns: [
        { speaker: "agent", start_sec: 0, end_sec: 3, text: "Em chào anh.", confidence: 0.95 },
        { speaker: "customer", start_sec: 3.2, end_sec: 6, text: "Thanh toán sao em? Có hóa đơn không?", confidence: 0.9 },
        { speaker: "agent", start_sec: 6.1, end_sec: 9, text: "Anh chuyển khoản giúp em nhé.", confidence: 0.92 },
      ],
    });
    setProcessResult(res.data);
    setSource(res.source);
  }

  async function runLowQuality() {
    const res = await api.audioIntelligenceProcess({
      file_meta: {
        file_id: "demo-bad",
        extension: ".mp3",
        integrity_ok: true,
        duration_sec: 20,
        sample_rate: 16000,
        channels: 1,
      },
      quality_hints: { quality_score: 25, noise: 0.85 },
      transcript_turns: [{ speaker: "agent", start_sec: 0, end_sec: 1, text: "alo", confidence: 0.8 }],
    });
    setProcessResult(res.data);
    setSource(res.source);
  }

  async function loadQuality() {
    const res = await api.audioIntelligenceQuality();
    setQuality(res.data);
    setSource(res.source);
  }

  const widgets = (dashboard?.widgets as Record<string, unknown> | undefined) || {};
  const gate = (processResult?.quality_gate as Record<string, unknown> | undefined) || {};
  const blocked = Boolean(processResult?.blocked);
  const scoringAllowed = Boolean(processResult?.scoring_allowed);

  const queueHint = useMemo(() => queueNote, [queueNote]);

  return (
    <AppShell>
      <PageHeader
        title="Audio Intelligence Engine"
        description="Upload → Repair → Diarization → Transcript → Evidence. Scoring is blocked until quality gate passes."
        actions={<SourcePill source={source} />}
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadDashboard}>
          Load Dashboard
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={runProcess}>
          Process Good Call
        </button>
        <button className="rounded bg-amber-700 px-3 py-1.5 text-sm text-white" onClick={runLowQuality}>
          Process Low-Quality (must block)
        </button>
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white" onClick={loadQuality}>
          Quality Snapshot
        </button>
      </div>

      <div
        className="mb-4 rounded border border-dashed border-slate-400 bg-slate-50 p-6 text-sm text-slate-700"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const n = e.dataTransfer.files?.length || 0;
          setQueueNote(`Queued ${n} file(s). Bulk upload supports 1000+ with retry/ETA.`);
        }}
      >
        <strong>Upload zone</strong>
        <p className="mt-1">{queueHint}</p>
        <p className="mt-1 text-xs text-slate-500">MP3 WAV M4A AAC OGG FLAC · MP4 MOV AVI MKV WEBM · ZIP RAR</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Panel title="Uploads"><p className="text-2xl font-semibold">{String(widgets.uploads ?? "—")}</p></Panel>
        <Panel title="Repairs"><p className="text-2xl font-semibold">{String(widgets.repairs ?? "—")}</p></Panel>
        <Panel title="Transcripts"><p className="text-2xl font-semibold">{String(widgets.transcripts ?? "—")}</p></Panel>
        <Panel title="Blocked Analysis"><p className="text-2xl font-semibold">{String(widgets.blocked_analysis ?? "—")}</p></Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Latest Process">
          <ul className="space-y-1 text-sm">
            <li>Blocked: {String(blocked)}</li>
            <li>Scoring allowed: {String(scoringAllowed)}</li>
            <li>Reason: {String(processResult?.block_reason ?? "—")}</li>
            <li>Gate passed: {String(gate.passed ?? "—")}</li>
            <li>Buying signals: {String(((processResult?.buying_signals as unknown[]) || []).length)}</li>
            <li>Objections: {String(((processResult?.objections as unknown[]) || []).length)}</li>
          </ul>
        </Panel>
        <Panel title="Quality Snapshot">
          <ul className="space-y-1 text-sm">
            <li>OK: {String(quality?.ok ?? "—")}</li>
            <li>Gate enforced: always — no scoring below threshold</li>
          </ul>
        </Panel>
      </div>
    </AppShell>
  );
}
