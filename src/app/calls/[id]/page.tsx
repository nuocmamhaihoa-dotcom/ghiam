"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { OutcomeBadge, ScoreRing, SectionTitle } from "@/components/ui";
import { loadCalls, type StoredCall } from "@/lib/store";

export default function CallDetailPage() {
  const params = useParams<{ id: string }>();
  const [call, setCall] = useState<StoredCall | null>(null);

  useEffect(() => {
    setCall(loadCalls().find((c) => c.id === params.id) ?? null);
  }, [params.id]);

  if (!call) {
    return (
      <div className="space-y-4">
        <p>Không tìm thấy cuộc gọi.</p>
        <Link href="/calls" className="text-[var(--accent)]">← Quay lại</Link>
      </div>
    );
  }

  const a = call.analysis;

  return (
    <div className="space-y-6">
      <Link href="/calls" className="text-sm text-[var(--accent)]">← Kho cuộc gọi</Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl">{call.title}</h1>
          <p className="mt-1 text-[var(--muted)]">{call.industry} · {call.product} · {call.agentName}</p>
          <div className="mt-3"><OutcomeBadge outcome={call.outcome} /></div>
        </div>
        <ScoreRing score={a.overallScore} label="Tổng điểm" />
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
          <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]">Opening</div>
          <div className="mt-1 font-[family-name:var(--font-display)] text-2xl">{a.openingScore}</div>
          <p className="mt-2 text-sm text-[var(--muted)]">{a.openingSummary}</p>
        </div>
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
          <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]">Closing</div>
          <div className="mt-1 font-[family-name:var(--font-display)] text-2xl">{a.closingScore}</div>
          <p className="mt-2 text-sm text-[var(--muted)]">{a.closingSummary}</p>
        </div>
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
          <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]">Tốc độ nói</div>
          <div className="mt-1 font-[family-name:var(--font-display)] text-2xl">{a.speakingRateWpm}</div>
          <p className="mt-2 text-sm text-[var(--muted)]">WPM · pause {(a.pauseRatio * 100).toFixed(0)}%</p>
        </div>
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
          <div className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]">Ngữ điệu</div>
          <div className="mt-1 font-[family-name:var(--font-display)] text-xl">{a.toneLabel}</div>
          <p className="mt-2 text-sm text-[var(--muted)]">{a.toneNotes}</p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle title="Giai đoạn cuộc gọi" />
          <ul className="space-y-2 text-sm">
            <li>Opening: {a.stages.opening}</li>
            <li>Discovery: {a.stages.discovery}</li>
            <li>Pitch: {a.stages.pitch}</li>
            <li>Objection: {a.stages.objection}</li>
            <li>Close: {a.stages.close}</li>
          </ul>
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle title="Từ khóa & tín hiệu đồng ý" />
          <p className="text-sm"><span className="text-[var(--muted)]">Keywords:</span> {a.keywordsHit.join(", ") || "—"}</p>
          <p className="mt-2 text-sm"><span className="text-[var(--muted)]">Agreement:</span> {a.agreementSignals.join(", ") || "—"}</p>
        </div>
      </div>

      <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <SectionTitle
            title="Tiêu chí ChốtKiểm"
            subtitle={`${a.chotKiem.requiredPassed}/${a.chotKiem.requiredCount} bắt buộc · ${a.chotKiem.closeOutcomeLabel}`}
          />
          <div className="text-right text-sm">
            <div className={a.chotKiem.complete ? "text-[var(--good)]" : "text-[var(--bad)]"}>
              {a.chotKiem.complete ? "Đủ tiêu chí" : "Thiếu tiêu chí"}
            </div>
            <div className="text-[var(--muted)]">Pass rate {a.chotKiem.passRate}%</div>
          </div>
        </div>
        <p className="mb-4 text-sm text-[var(--muted)]">{a.chotKiem.summary}</p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {a.chotKiem.criteria.map((c) => (
            <div
              key={c.key}
              className={`rounded-2xl border p-3 ${
                c.passed
                  ? "border-[var(--good)]/30 bg-[var(--good)]/5"
                  : "border-[var(--bad)]/30 bg-[var(--bad)]/5"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="text-sm font-medium">{c.shortLabel}</div>
                <span className={`text-xs ${c.passed ? "text-[var(--good)]" : "text-[var(--bad)]"}`}>
                  {c.passed ? "Đạt" : "Thiếu"}
                  {c.required ? "" : " · optional"}
                </span>
              </div>
              <p className="mt-1 text-xs text-[var(--muted)]">{c.value}</p>
              {c.evidence ? (
                <p className="mt-2 line-clamp-2 text-xs text-[var(--accent)]">{c.evidence}</p>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <SectionTitle title="Xử lý từ chối" />
        {a.objections.length === 0 ? (
          <p className="text-sm text-[var(--muted)]">Không phát hiện từ chối rõ.</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {a.objections.map((o, i) => (
              <div key={i} className="rounded-2xl border border-[var(--line)] p-4">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs uppercase tracking-[0.12em] text-[var(--accent-2)]">{o.type}</div>
                  <span className={`text-xs ${o.handledWell ? "text-[var(--good)]" : "text-[var(--bad)]"}`}>
                    {o.handledWell ? "Xử lý tốt" : "Cần cải thiện"}
                  </span>
                </div>
                <p className="mt-2 text-sm text-[var(--muted)]">KH: {o.customerLine}</p>
                <p className="mt-1 text-sm">Sale: {o.agentReply}</p>
                <p className="mt-2 text-xs text-[var(--accent)]">{o.tip}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <SectionTitle title="Gợi ý coaching" />
        <ul className="list-disc space-y-2 pl-5 text-sm">
          {a.coachingTips.map((t, i) => <li key={i}>{t}</li>)}
        </ul>
      </div>

      <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <SectionTitle title="Transcript" />
        <pre className="whitespace-pre-wrap text-sm leading-relaxed">{call.transcript}</pre>
      </div>
    </div>
  );
}
