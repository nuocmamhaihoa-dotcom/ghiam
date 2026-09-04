"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Metric,
  OutcomeBadge,
  ScoreRing,
  SectionTitle,
} from "@/components/ui";
import type { DeepCallAnalysis } from "@/lib/deepCallAnalysis";
import type { RecordingMeta } from "@/lib/recordingLibrary";

type Payload = {
  meta: RecordingMeta;
  transcript: string;
  analysis: DeepCallAnalysis;
};

export default function RecordingDetailPage() {
  const params = useParams<{ id: string }>();
  const [rec, setRec] = useState<Payload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!params.id) return;
    fetch(`/api/recordings/${params.id}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) throw new Error(d.error || "Not found");
        setRec(d.recording);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Lỗi tải"));
  }, [params.id]);

  if (error) {
    return (
      <div className="space-y-4">
        <p className="text-rose-700">{error}</p>
        <Link href="/recordings" className="text-[var(--accent)]">
          ← Thư viện ghi âm
        </Link>
      </div>
    );
  }

  if (!rec) {
    return <p className="text-[var(--muted)]">Đang tải phân tích…</p>;
  }

  const { meta: m, analysis: a, transcript } = rec;

  return (
    <div className="space-y-8">
      <Link href="/recordings" className="text-sm text-[var(--accent)]">
        ← Thư viện ghi âm
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl">
            {m.title}
          </h1>
          <p className="mt-1 text-[var(--muted)]">
            {m.agentName} · {m.phoneMasked} ·{" "}
            {new Date(m.createdAt).toLocaleString("vi-VN")}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <OutcomeBadge outcome={m.outcome} />
            <span className="text-sm text-[var(--muted)]">
              Grade {m.grade || "—"} · Score {m.overallScore ?? "—"} ·{" "}
              {m.durationSec}s
            </span>
          </div>
          {m.callSummary ? (
            <p className="mt-3 max-w-2xl text-sm text-[var(--muted)]">
              {m.callSummary}
            </p>
          ) : null}
        </div>
        <ScoreRing score={a.readinessScore} label="Readiness tái tạo" />
      </div>

      {m.hasAudio ? (
        <audio
          controls
          className="w-full"
          src={`/api/recordings/${m.id}/audio`}
        />
      ) : null}

      <div className="grid gap-4 md:grid-cols-4">
        <Metric
          label="Talk ratio TVV"
          value={`${Math.round(a.speakers.agentTalkRatio * 100)}%`}
        />
        <Metric
          label="Tốc độ nói"
          value={`${a.delivery.speakingRateWpm}`}
          hint="từ/phút (ước lượng)"
        />
        <Metric label="Lịch sự" value={`${a.delivery.politenessScore}`} />
        <Metric label="Đồng cảm" value={`${a.delivery.empathyScore}`} />
      </div>

      <section className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <SectionTitle title="Tóm tắt sâu" subtitle={a.summary.headline} />
        <div className="grid gap-4 md:grid-cols-3">
          <div>
            <h3 className="text-sm font-medium">Điểm mạnh</h3>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-sm">
              {a.summary.whatWorked.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="text-sm font-medium">Điểm yếu</h3>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-sm">
              {a.summary.whatFailed.map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="text-sm font-medium">Một việc cần sửa</h3>
            <p className="mt-2 text-sm">{a.summary.oneThingToFix}</p>
          </div>
        </div>
      </section>

      <section>
        <SectionTitle title="Timeline giai đoạn bán hàng" />
        <div className="grid gap-3 md:grid-cols-2">
          {a.stages.map((s) => (
            <div
              key={`${s.key}-${s.startSec}`}
              className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4"
            >
              <div className="flex items-center justify-between gap-2">
                <h3 className="font-medium">{s.label}</h3>
                <span className="text-sm text-[var(--muted)]">{s.score}/100</span>
              </div>
              <p className="mt-1 text-xs text-[var(--muted)]">
                {s.startSec}s – {s.endSec}s
              </p>
              <p className="mt-2 text-sm">{s.summary}</p>
              {s.keyLines[0] ? (
                <p className="mt-2 text-xs italic text-[var(--muted)]">
                  “{s.keyLines[0]}”
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4">
          <h3 className="font-medium">Opening</h3>
          <ul className="mt-2 space-y-1 text-sm text-[var(--muted)]">
            <li>Chào: {a.opening.greeting || "—"}</li>
            <li>Xin phép: {a.opening.permissionAsk || "—"}</li>
            <li>GT công ty: {a.opening.companyIntro || "—"}</li>
            <li>Hook: {a.opening.valueHook || "—"}</li>
            <li>Điểm opening: {a.opening.score}</li>
          </ul>
        </div>
        <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4">
          <h3 className="font-medium">Trích xuất chốt đơn</h3>
          <ul className="mt-2 space-y-1 text-sm text-[var(--muted)]">
            <li>Tên KH: {a.extracted.customerName || "—"}</li>
            <li>Sản phẩm: {a.extracted.product || "—"}</li>
            <li>Số lượng: {a.extracted.quantity || "—"}</li>
            <li>Giá: {a.extracted.price || "—"}</li>
            <li>Địa chỉ: {a.extracted.address || "—"}</li>
            <li>Giao hàng: {a.extracted.deliveryPromise || "—"}</li>
            <li>SĐT nhắc: {a.extracted.phoneMention || "—"}</li>
          </ul>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <div>
          <SectionTitle title="Câu hỏi discovery" />
          <ul className="list-disc space-y-1 pl-4 text-sm">
            {a.discoveryQuestions.length ? (
              a.discoveryQuestions.map((q) => <li key={q}>{q}</li>)
            ) : (
              <li className="text-[var(--muted)]">Không phát hiện</li>
            )}
          </ul>
        </div>
        <div>
          <SectionTitle title="Pitch points" />
          <ul className="list-disc space-y-1 pl-4 text-sm">
            {a.pitchPoints.length ? (
              a.pitchPoints.map((q) => <li key={q}>{q}</li>)
            ) : (
              <li className="text-[var(--muted)]">Không phát hiện</li>
            )}
          </ul>
        </div>
      </section>

      <section>
        <SectionTitle title="Xử lý từ chối" />
        <div className="space-y-3">
          {a.objections.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">Không có objection rõ.</p>
          ) : (
            a.objections.map((o, i) => (
              <div
                key={`${o.type}-${i}`}
                className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{o.type}</span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs ${
                      o.handledWell
                        ? "bg-emerald-100 text-emerald-800"
                        : "bg-amber-100 text-amber-900"
                    }`}
                  >
                    {o.handledWell ? "Xử lý tốt" : "Cần cải thiện"}
                  </span>
                </div>
                <p className="mt-2 text-sm">
                  <span className="text-[var(--muted)]">KH:</span> {o.customerLine}
                </p>
                <p className="mt-1 text-sm">
                  <span className="text-[var(--muted)]">TVV:</span> {o.agentReply}
                </p>
                <p className="mt-2 text-xs text-[var(--muted)]">
                  Kỹ thuật: {o.technique} · Tip: {o.tip}
                </p>
              </div>
            ))
          )}
        </div>
      </section>

      <section>
        <SectionTitle title="Nỗ lực chốt" />
        <div className="space-y-2">
          {a.closeAttempts.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">Chưa có câu chốt rõ.</p>
          ) : (
            a.closeAttempts.map((c, i) => (
              <div
                key={`${c.type}-${i}`}
                className="rounded-lg border border-[var(--line)] px-3 py-2 text-sm"
              >
                <span className="font-medium">{c.type}</span> · {c.result}
                <div className="text-[var(--muted)]">{c.line}</div>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <div>
          <SectionTitle title="Tín hiệu mua" />
          <ul className="list-disc space-y-1 pl-4 text-sm">
            {a.buyingSignals.length ? (
              a.buyingSignals.map((x) => <li key={x}>{x}</li>)
            ) : (
              <li className="text-[var(--muted)]">—</li>
            )}
          </ul>
        </div>
        <div>
          <SectionTitle title="Rủi ro mất đơn" />
          <ul className="list-disc space-y-1 pl-4 text-sm">
            {a.riskSignals.length ? (
              a.riskSignals.map((x) => <li key={x}>{x}</li>)
            ) : (
              <li className="text-[var(--muted)]">—</li>
            )}
          </ul>
        </div>
        <div>
          <SectionTitle title="Kỹ thuật thuyết phục" />
          <ul className="list-disc space-y-1 pl-4 text-sm">
            {a.persuasionTechniques.length ? (
              a.persuasionTechniques.map((x) => <li key={x}>{x}</li>)
            ) : (
              <li className="text-[var(--muted)]">—</li>
            )}
          </ul>
        </div>
      </section>

      <section>
        <SectionTitle
          title="Kịch bản tái tạo cuộc gọi chốt đơn"
          subtitle="Dùng làm mẫu luyện / coaching / TTS"
        />
        <div className="space-y-3">
          {a.recreationScript.map((step, i) => (
            <div
              key={`${step.stage}-${i}`}
              className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4"
            >
              <div className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">
                {step.stage} · {step.goal}
              </div>
              <p className="mt-2 text-sm font-medium">“{step.modelLine}”</p>
              <p className="mt-1 text-xs text-[var(--muted)]">{step.why}</p>
              {step.alternatives.length ? (
                <p className="mt-2 text-xs text-[var(--muted)]">
                  Biến thể: {step.alternatives.join(" | ")}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <section>
        <SectionTitle title="Transcript" />
        <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 text-sm leading-relaxed">
          {transcript || "(không có transcript)"}
        </pre>
      </section>
    </div>
  );
}
