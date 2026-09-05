"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { CoachingPanel } from "@/components/CoachingPanel";
import { EmotionTimeline } from "@/components/EmotionTimeline";
import { EvidenceTimeline } from "@/components/EvidenceTimeline";
import { RadarDNA } from "@/components/RadarDNA";
import { RevenueLeakChart } from "@/components/RevenueLeakChart";
import { RootCauseTree } from "@/components/RootCauseTree";
import { ScoreGauge } from "@/components/ScoreGauge";
import { Badge, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import {
  SEVERITY_LABELS,
  STAGE_LABELS,
  VERDICT_LABELS,
  formatDateTime,
  formatNumber,
  scoreFill,
  scoreTone,
} from "@/lib/format";
import type { AnalysisResult, EvidenceSpan } from "@/lib/types";

export default function CallDetailPage() {
  const params = useParams<{ id: string }>();
  const callId = params.id;
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [selected, setSelected] = useState<EvidenceSpan | undefined>();

  useEffect(() => {
    void (async () => {
      const res = await api.getCallAnalysis(callId);
      setAnalysis(res.data);
      setSource(res.source);
      setSelected(res.data.evidence[0]);
    })();
  }, [callId]);

  if (!analysis) {
    return (
      <AppShell>
        <div className="animate-pulse-soft text-slate-400">
          Đang tải phân tích cuộc gọi…
        </div>
      </AppShell>
    );
  }

  const stages = Object.entries(analysis.stage_scores);

  return (
    <AppShell>
      <PageHeader
        title={`Cuộc gọi ${analysis.meta.call_id}`}
        description={`Rulebook ${analysis.meta.rulebook_version} · Pipeline ${analysis.meta.pipeline_version} · ${formatDateTime(analysis.meta.analyzed_at)}`}
        actions={
          <div className="flex items-center gap-2">
            <SourcePill source={source} />
            <Link
              href="/calls"
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-900"
            >
              ← Danh sách
            </Link>
          </div>
        }
      />

      <div className="mb-4 flex flex-wrap gap-2 text-xs">
        <Badge tone="teal">{analysis.meta.status}</Badge>
        {analysis.meta.industry_code ? (
          <Badge>{analysis.meta.industry_code}</Badge>
        ) : null}
        {analysis.meta.sop_id ? (
          <Badge tone="sky">{analysis.meta.sop_id}</Badge>
        ) : null}
        <Badge>trace {analysis.meta.trace_id}</Badge>
      </div>

      <div className="mb-6 grid gap-4 xl:grid-cols-[220px_1fr_1fr]">
        <Panel className="flex items-center justify-center">
          <ScoreGauge
            score={analysis.score}
            label="Điểm tổng"
            sublabel={
              analysis.score == null
                ? "Partial / IE"
                : analysis.score >= 80
                  ? "Đạt chuẩn"
                  : "Cần cải thiện"
            }
          />
        </Panel>

        <Panel title="Điểm theo giai đoạn">
          <div className="space-y-3">
            {stages.map(([key, value]) => (
              <div key={key}>
                <div className="mb-1 flex justify-between text-xs">
                  <span className="text-slate-300">
                    {STAGE_LABELS[key] || key}
                  </span>
                  <span className={scoreTone(value)}>
                    {value == null ? "IE" : formatNumber(value, 0)}
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${value == null ? 8 : value}%`,
                      background:
                        value == null ? "#f59e0b" : scoreFill(value),
                      opacity: value == null ? 0.45 : 1,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Vi phạm / rule items">
          <ul className="max-h-64 space-y-2 overflow-y-auto pr-1 text-sm">
            {analysis.violations.map((v) => (
              <li
                key={`${v.rule_id}-${v.message}`}
                className="rounded-lg border border-slate-800 px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-teal-300">
                    {v.rule_id}
                  </span>
                  <Badge
                    tone={
                      v.status === "fail"
                        ? "rose"
                        : v.status === "Insufficient Evidence"
                          ? "amber"
                          : "emerald"
                    }
                  >
                    {VERDICT_LABELS[v.status] || v.status}
                  </Badge>
                  <Badge>{SEVERITY_LABELS[v.severity] || v.severity}</Badge>
                  {v.deduction > 0 ? (
                    <span className="text-xs text-rose-300">−{v.deduction}</span>
                  ) : null}
                </div>
                <p className="mt-1 text-slate-300">{v.message}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="mb-6 grid gap-4 xl:grid-cols-2">
        <Panel title="Evidence timeline">
          <EvidenceTimeline
            evidence={analysis.evidence}
            selectedId={selected?.evidence_id}
            onSelect={setSelected}
          />
          {selected ? (
            <div className="mt-3 rounded-lg border border-teal-500/20 bg-teal-500/5 px-3 py-2 text-xs text-slate-400">
              Đang chọn <span className="font-mono text-teal-300">{selected.evidence_id}</span> ·
              turn #{selected.turn_index} · conf{" "}
              {(selected.confidence * 100).toFixed(0)}%
            </div>
          ) : null}
        </Panel>
        <Panel>
          <EmotionTimeline points={analysis.emotion_timeline || []} />
        </Panel>
      </div>

      <div className="mb-6 grid gap-4 xl:grid-cols-3">
        <Panel>
          <RootCauseTree rootCause={analysis.root_cause} />
        </Panel>
        <Panel>
          <CoachingPanel coaching={analysis.coaching} />
        </Panel>
        <Panel>
          <RevenueLeakChart leak={analysis.revenue_leak} />
        </Panel>
      </div>

      {analysis.conversation_dna ? (
        <Panel>
          <RadarDNA dna={analysis.conversation_dna} />
        </Panel>
      ) : null}
    </AppShell>
  );
}
