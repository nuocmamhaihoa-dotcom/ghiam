"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { CoachingPanel } from "@/components/CoachingPanel";
import { EmotionTimeline } from "@/components/EmotionTimeline";
import { ScoreGauge } from "@/components/ScoreGauge";
import { Badge, PageHeader, Panel, SourcePill } from "@/components/ui";
import { api } from "@/lib/api";
import { getStoredUser } from "@/lib/auth";
import {
  STAGE_LABELS,
  formatDateTime,
  formatVnd,
  scoreTone,
} from "@/lib/format";
import type {
  AnalysisResult,
  CallSummary,
  CoachingPlan,
  ConversationDNA,
  StageKey,
} from "@/lib/types";

function badgeTone(
  score: number
): "slate" | "teal" | "amber" | "rose" | "emerald" | "sky" {
  if (score >= 80) return "emerald";
  if (score >= 60) return "sky";
  if (score >= 40) return "amber";
  return "rose";
}

export default function EmployeePortalPage() {
  const me = getStoredUser();
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [plans, setPlans] = useState<CoachingPlan[]>([]);
  const [selectedCallId, setSelectedCallId] = useState("");
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [dna, setDna] = useState<ConversationDNA | null>(null);
  const [source, setSource] = useState<"api" | "demo">("demo");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [callPack, planPack] = await Promise.all([
          api.listCalls({ limit: 40 }),
          api.listCoachingPlans(),
        ]);
        if (cancelled) return;
        const mine = me?.id
          ? callPack.data.data.filter((c) => c.agent_user_id === me.id)
          : callPack.data.data;
        const myPlans = me?.id
          ? planPack.data.filter((p) => p.agent_user_id === me.id)
          : planPack.data;
        const rows = mine.length ? mine : callPack.data.data;
        const planRows = myPlans.length ? myPlans : planPack.data;
        setCalls(rows);
        setPlans(planRows);
        setSelectedCallId(rows[0]?.id ?? "");
        setSource(
          callPack.source === "api" || planPack.source === "api" ? "api" : "demo"
        );
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Không tải được portal");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [me?.id]);

  useEffect(() => {
    if (!selectedCallId) return;
    let cancelled = false;
    void (async () => {
      try {
        const [analysisPack, dnaPack] = await Promise.all([
          api.getCallAnalysis(selectedCallId),
          api.getConversationDna(selectedCallId),
        ]);
        if (cancelled) return;
        setAnalysis(analysisPack.data);
        setDna(dnaPack.data);
        if (analysisPack.source === "api" || dnaPack.source === "api") {
          setSource("api");
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Không tải được phân tích cuộc gọi"
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedCallId]);

  const selectedCall = useMemo(
    () => calls.find((c) => c.id === selectedCallId) ?? null,
    [calls, selectedCallId]
  );

  const stageEntries = Object.entries(analysis?.stage_scores ?? {}) as [
    StageKey,
    number | null,
  ][];

  return (
    <AppShell>
      <PageHeader
        title="Employee Portal"
        description={`${me?.full_name ?? "Agent"} · điểm cá nhân, evidence, coaching và Conversation DNA`}
        actions={<SourcePill source={source} />}
      />

      {error ? (
        <div className="mb-4 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      ) : null}

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Cuộc gọi của tôi">
          <div className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
            {calls.map((call) => (
              <button
                key={call.id}
                type="button"
                onClick={() => setSelectedCallId(call.id)}
                className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition ${
                  selectedCallId === call.id
                    ? "border-teal-500/50 bg-teal-500/10"
                    : "border-slate-800 hover:border-slate-600"
                }`}
              >
                <div>
                  <div className="text-sm font-medium text-slate-100">
                    {call.external_call_id}
                  </div>
                  <div className="text-xs text-slate-500">
                    {call.customer_phone_masked} · {formatDateTime(call.started_at)}
                  </div>
                </div>
                <Badge
                  tone={
                    call.overall_score == null
                      ? "amber"
                      : badgeTone(call.overall_score)
                  }
                >
                  {call.overall_score ?? "IE"}
                </Badge>
              </button>
            ))}
            {!calls.length ? (
              <p className="text-sm text-slate-500">Chưa có cuộc gọi.</p>
            ) : null}
          </div>
        </Panel>

        <Panel title={`Điểm & KPI · ${selectedCall?.external_call_id ?? "—"}`}>
          <div className="flex flex-wrap items-center gap-6">
            <ScoreGauge
              score={analysis?.score ?? selectedCall?.overall_score ?? null}
              sublabel={selectedCall?.result ?? undefined}
            />
            <div className="min-w-[14rem] flex-1 space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <span className="text-slate-400">Root cause</span>
                <strong className="text-slate-100">
                  {analysis?.root_cause.primary_code ?? "—"}
                </strong>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-slate-400">Revenue leak</span>
                <strong className={scoreTone(40)}>
                  {formatVnd(analysis?.revenue_leak.estimated_loss_vnd)}
                </strong>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-slate-400">IE status</span>
                <Badge tone="amber">{analysis?.revenue_leak.status ?? "—"}</Badge>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-slate-400">Call status</span>
                <Badge>{selectedCall?.status ?? "—"}</Badge>
              </div>
            </div>
          </div>
        </Panel>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Stage Scores">
          <div className="space-y-2">
            {stageEntries.map(([stage, value]) => (
              <div
                key={stage}
                className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 px-3 py-2"
              >
                <span className="text-sm text-slate-300">
                  {STAGE_LABELS[stage] ?? stage}
                </span>
                <Badge tone={value == null ? "amber" : badgeTone(value)}>
                  {value == null ? "IE" : value.toFixed(1)}
                </Badge>
              </div>
            ))}
            {!stageEntries.length ? (
              <p className="text-sm text-slate-500">Chưa có stage score.</p>
            ) : null}
          </div>
        </Panel>
        <Panel title="Emotion Timeline">
          <EmotionTimeline points={analysis?.emotion_timeline ?? []} />
        </Panel>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <Panel title="Violations & Evidence">
          <div className="space-y-3">
            {(analysis?.violations ?? []).map((v) => (
              <div
                key={`${v.rule_id}-${v.message}`}
                className="rounded-lg border border-slate-800 bg-slate-950/40 p-3"
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="font-mono text-xs text-teal-300">{v.rule_id}</span>
                  <Badge tone={v.severity === "critical" ? "rose" : "amber"}>
                    {v.severity}
                  </Badge>
                </div>
                <div className="text-sm text-slate-200">{v.message}</div>
                <div className="mt-1 text-xs text-slate-500">
                  Deduction {v.deduction} · refs {v.evidence_refs.join(", ") || "—"}
                </div>
              </div>
            ))}
            {(analysis?.evidence ?? []).slice(0, 6).map((e) => (
              <div
                key={e.evidence_id}
                className="rounded-lg border border-slate-800 px-3 py-2"
              >
                <div className="mb-1 flex justify-between gap-2 text-xs text-slate-500">
                  <span className="font-mono">{e.evidence_id}</span>
                  <span>
                    {e.audio_ts_start.toFixed(1)}s – {e.audio_ts_end.toFixed(1)}s
                  </span>
                </div>
                <p className="text-sm text-slate-200">“{e.quote}”</p>
              </div>
            ))}
            {!analysis?.violations?.length && !analysis?.evidence?.length ? (
              <p className="text-sm text-slate-500">Không có violation/evidence.</p>
            ) : null}
          </div>
        </Panel>

        <Panel title="Coaching cho bạn">
          {analysis?.coaching ? (
            <CoachingPanel coaching={analysis.coaching} />
          ) : (
            <p className="text-sm text-slate-500">Chưa có coaching từ cuộc gọi này.</p>
          )}
          <div className="mt-4 space-y-2 border-t border-slate-800 pt-4">
            {plans.slice(0, 5).map((plan) => (
              <div
                key={plan.id}
                className="flex items-start justify-between gap-3 rounded-lg border border-slate-800 px-3 py-2"
              >
                <div>
                  <div className="text-sm font-medium text-slate-100">
                    {plan.focus_areas.join(" · ") || plan.id}
                  </div>
                  <div className="text-xs text-slate-500">
                    {plan.items
                      .slice(0, 2)
                      .map((i) => i.title)
                      .join(" · ")}
                  </div>
                </div>
                <Badge tone="teal">{plan.status}</Badge>
              </div>
            ))}
            {!plans.length ? (
              <p className="text-sm text-slate-500">Chưa có coaching plan.</p>
            ) : null}
            <Link
              href="/coaching"
              className="inline-block text-sm text-teal-300 hover:text-teal-200"
            >
              Mở trang Coaching đầy đủ →
            </Link>
          </div>
        </Panel>
      </div>

      <div className="mb-4">
        <Panel title="Conversation DNA">
          <p className="mb-3 text-sm text-slate-400">{dna?.summary ?? "—"}</p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(dna?.dimensions ?? []).map((dim) => (
              <div
                key={dim.key}
                className="rounded-lg border border-slate-800 px-3 py-2"
              >
                <div className="text-xs text-slate-500">{dim.label}</div>
                <div className={`text-lg font-semibold ${scoreTone(dim.score)}`}>
                  {dim.score.toFixed(1)}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel title="Canonical Analysis JSON">
        <p className="mb-2 text-xs text-slate-500">
          score · stage_scores · violations · evidence · root_cause · coaching · revenue_leak
        </p>
        <pre className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/70 p-3 text-xs text-slate-300">
          {JSON.stringify(
            {
              score: analysis?.score ?? null,
              stage_scores: analysis?.stage_scores ?? {},
              violations: analysis?.violations ?? [],
              evidence: analysis?.evidence ?? [],
              root_cause: analysis?.root_cause ?? null,
              coaching: analysis?.coaching ?? null,
              revenue_leak: analysis?.revenue_leak ?? null,
            },
            null,
            2
          )}
        </pre>
      </Panel>
    </AppShell>
  );
}
