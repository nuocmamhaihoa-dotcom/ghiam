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

      {a.qualityScorecard ? (
        <section className="space-y-4 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <SectionTitle
              title="Bảng điểm chất lượng cuộc gọi"
              subtitle={`${a.qualityScorecard.closeOutcomeLabel} · ChốtKiểm ${a.qualityScorecard.chotKiemComplete ? "đủ tiêu chí" : "thiếu tiêu chí"}`}
            />
            <ScoreRing
              score={a.qualityScorecard.overallScore}
              label={`QA ${a.qualityScorecard.grade}`}
            />
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {a.qualityScorecard.metrics.map((m) => (
              <div
                key={m.key}
                className="rounded-xl border border-[var(--line)] bg-white/70 p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{m.label}</span>
                  <span
                    className={`text-sm ${m.passed ? "text-emerald-700" : "text-rose-700"}`}
                  >
                    {m.score}/100
                  </span>
                </div>
                {m.evidence ? (
                  <p className="mt-1 text-xs text-[var(--muted)]">{m.evidence}</p>
                ) : null}
                {!m.passed && m.tip ? (
                  <p className="mt-1 text-xs text-amber-800">{m.tip}</p>
                ) : null}
              </div>
            ))}
          </div>
          {a.qualityScorecard.gaps.length ? (
            <p className="text-sm text-[var(--muted)]">
              Khoảng trống: {a.qualityScorecard.gaps.join(" · ")}
            </p>
          ) : null}
        </section>
      ) : null}

      {a.aiClonePack ? (
        <section className="space-y-4 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <SectionTitle
              title="Gói dữ liệu tái tạo cuộc gọi (AI Clone)"
              subtitle={a.aiClonePack.targetCustomerProfile}
            />
            <ScoreRing
              score={a.aiClonePack.cloneScore}
              label={a.aiClonePack.cloneReady ? "Clone Ready" : "Chưa sẵn sàng"}
            />
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <Metric label="Giọng / tone" value={a.aiClonePack.persona.tone} />
            <Metric
              label="Tốc độ mẫu"
              value={`${a.aiClonePack.persona.paceWpm}`}
              hint="từ/phút"
            />
            <Metric
              label="Assertiveness"
              value={`${a.aiClonePack.persona.assertiveness}`}
            />
          </div>
          <div>
            <h3 className="text-sm font-medium">Slot cần điền khi gọi khách mới</h3>
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {a.aiClonePack.slots.map((s) => (
                <div
                  key={s.key}
                  className="rounded-xl border border-[var(--line)] bg-white/70 p-3 text-sm"
                >
                  <div className="font-medium">
                    [{s.key}] {s.label}
                    {s.required ? " *" : ""}
                  </div>
                  <div className="mt-1 text-[var(--muted)]">
                    Mẫu: {s.value || s.example}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="text-sm font-medium">Must say</h3>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-sm">
                {a.aiClonePack.mustSay.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="text-sm font-medium">Avoid say</h3>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-sm">
                {a.aiClonePack.avoidSay.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
          </div>
          <div>
            <h3 className="text-sm font-medium">Kịch bản thay biến</h3>
            <div className="mt-2 space-y-2">
              {a.aiClonePack.variableScript.map((step, i) => (
                <div
                  key={`${step.stage}-${i}`}
                  className="rounded-xl border border-[var(--line)] bg-white/70 p-3"
                >
                  <div className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">
                    {step.stage} · {step.goal}
                  </div>
                  <p className="mt-1 text-sm">“{step.template}”</p>
                  {step.fillHints.length ? (
                    <p className="mt-1 text-xs text-[var(--muted)]">
                      Điền: {step.fillHints.join(", ")}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
          {a.aiClonePack.objectionBranches.length ? (
            <div>
              <h3 className="text-sm font-medium">Nhánh xử lý từ chối</h3>
              <div className="mt-2 space-y-2">
                {a.aiClonePack.objectionBranches.map((b, i) => (
                  <div
                    key={`${b.trigger}-${i}`}
                    className="rounded-xl border border-[var(--line)] bg-white/70 p-3 text-sm"
                  >
                    <div className="font-medium">{b.trigger}</div>
                    <p className="mt-1 text-[var(--muted)]">KH: {b.customerLine}</p>
                    <p className="mt-1">TVV: {b.reply}</p>
                    <p className="mt-1 text-xs text-amber-800">{b.tip}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {a.aiClonePack.recreationNotes.length ? (
            <p className="text-xs text-[var(--muted)]">
              {a.aiClonePack.recreationNotes.join(" · ")}
            </p>
          ) : null}
        </section>
      ) : null}

      <section>
        <SectionTitle title="Transcript" />
        <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 text-sm leading-relaxed">
          {transcript || "(không có transcript)"}
        </pre>
      </section>
    </div>
  );
}
