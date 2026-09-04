"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Metric, OutcomeBadge, ScoreRing, SectionTitle } from "@/components/ui";
import { INDUSTRY_PLAYBOOKS } from "@/lib/analyzeCall";
import { getInsights, loadCalls, resetDemoCalls, type StoredCall } from "@/lib/store";

export default function HomePage() {
  const [calls, setCalls] = useState<StoredCall[]>([]);
  useEffect(() => setCalls(loadCalls()), []);
  const insights = getInsights(calls);

  return (
    <div className="space-y-8">
      <section className="grid gap-6 lg:grid-cols-[1.35fr_0.75fr]">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-[var(--accent)]">
            Kho ghi âm → Playbook bán hàng
          </p>
          <h1 className="mt-2 max-w-2xl font-[family-name:var(--font-display)] text-4xl leading-tight md:text-5xl">
            CallCraft đọc cuộc gọi và dạy sale cách mở đầu, xử lý từ chối, chốt đơn.
          </h1>
          <p className="mt-4 max-w-xl text-[var(--muted)]">
            MVP chạy trên transcript tiếng Việt. Độ gần coach người ~75–85% khi có nhãn kết quả.
            Gắn Whisper + prosody model để nâng ngữ điệu lên ~85–90%.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link href="/analyze" className="rounded-full bg-[var(--accent)] px-5 py-2.5 text-sm font-medium text-white">
              Phân tích cuộc gọi mới
            </Link>
            <button
              type="button"
              onClick={() => setCalls(resetDemoCalls())}
              className="rounded-full border border-[var(--line)] bg-[var(--panel)] px-5 py-2.5 text-sm"
            >
              Reset data demo
            </button>
          </div>
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-6">
          <div className="mb-3 flex items-center justify-between">
            <SectionTitle title="Độ tin cậy kỹ thuật" />
            <ScoreRing score={80} label="vs coach" />
          </div>
          <p className="text-sm text-[var(--muted)]">{insights.realismNote}</p>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Metric label="Win rate" value={`${insights.winRate}%`} hint={`${calls.length} cuộc gọi`} />
        <Metric label="Điểm TB" value={`${insights.avgOverall}`} />
        <Metric label="Opening TB" value={`${insights.avgOpening}`} />
        <Metric label="Tốc độ nói" value={`${insights.avgWpm}`} hint="từ/phút" />
        <Metric
          label="ChốtKiểm QA"
          value={`${insights.criteriaPassRate}%`}
          hint={`${insights.criteriaCompleteRate}% đủ tiêu chí`}
        />
      </section>

      <section className="grid gap-6 lg:grid-cols-3">
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle
            title="Tiêu chí ChốtKiểm hay thiếu"
            subtitle="Các mục bắt buộc sale hay bỏ sót (port từ 222.255.215.55)"
          />
          <div className="space-y-3">
            {insights.criteriaGaps.length === 0 ? (
              <p className="text-sm text-[var(--muted)]">Demo calls đạt khá đều — chưa thấy gap rõ.</p>
            ) : (
              insights.criteriaGaps.map((g) => (
                <div key={g.label} className="flex items-center justify-between rounded-xl bg-[var(--chip)]/70 px-3 py-2">
                  <div className="text-sm font-medium">{g.label}</div>
                  <div className="text-sm text-[var(--bad)]">{g.count} cuộc thiếu</div>
                </div>
              ))
            )}
          </div>
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle title="Từ khóa khiến khách đồng ý" subtitle="Tỉ lệ xuất hiện trong cuộc thắng" />
          <div className="space-y-3">
            {insights.topKeywords.map((k) => (
              <div key={k.keyword} className="flex items-center gap-3">
                <div className="w-28 text-sm font-medium">{k.keyword}</div>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--chip)]">
                  <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${k.lift}%` }} />
                </div>
                <div className="w-20 text-right text-sm text-[var(--muted)]">{k.lift}% · {k.count}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle title="Theo ngành hàng" />
          <div className="space-y-3">
            {insights.industries.map((row) => (
              <div key={row.industry} className="flex items-center justify-between rounded-xl bg-[var(--chip)]/70 px-3 py-2">
                <div>
                  <div className="font-medium">{row.industry}</div>
                  <div className="text-xs text-[var(--muted)]">{row.total} cuộc</div>
                </div>
                <div className="text-right text-sm">
                  <div>{row.winRate}% win</div>
                  <div className="text-[var(--muted)]">score {row.avgScore}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle title="Cách mở đầu thành công" />
          <div className="space-y-3">
            {insights.bestOpenings.map((s, i) => (
              <p key={i} className="prose-quote rounded-r-xl py-2 text-sm">{s}</p>
            ))}
          </div>
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle title="Cách chốt đơn hiệu quả" />
          <div className="space-y-3">
            {insights.bestCloses.map((s, i) => (
              <p key={i} className="prose-quote rounded-r-xl py-2 text-sm">{s}</p>
            ))}
          </div>
        </div>
      </section>

      <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <SectionTitle title="Xử lý từ chối tốt" subtitle="Mẫu phản hồi từ cuộc điểm cao" />
        <div className="grid gap-3 md:grid-cols-2">
          {insights.objectionTips.map((o, i) => (
            <div key={i} className="rounded-2xl border border-[var(--line)] p-4">
              <div className="text-xs uppercase tracking-[0.12em] text-[var(--accent-2)]">{o.type}</div>
              <p className="mt-2 text-sm text-[var(--muted)]">KH: {o.customerLine}</p>
              <p className="mt-1 text-sm">Sale: {o.agentReply}</p>
              <p className="mt-2 text-xs text-[var(--accent)]">{o.tip}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <div className="mb-4 flex items-end justify-between">
          <SectionTitle title="Cuộc gọi gần đây" />
          <Link href="/calls" className="text-sm text-[var(--accent)]">Xem tất cả</Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs uppercase tracking-[0.12em] text-[var(--muted)]">
              <tr>
                <th className="pb-3">Cuộc gọi</th>
                <th className="pb-3">Ngành</th>
                <th className="pb-3">Kết quả</th>
                <th className="pb-3">Score</th>
                <th className="pb-3">WPM</th>
              </tr>
            </thead>
            <tbody>
              {calls.slice(0, 6).map((c) => (
                <tr key={c.id} className="border-t border-[var(--line)]">
                  <td className="py-3">
                    <Link href={`/calls/${c.id}`} className="font-medium hover:text-[var(--accent)]">{c.title}</Link>
                    <div className="text-xs text-[var(--muted)]">{c.agentName}</div>
                  </td>
                  <td className="py-3">{c.industry}</td>
                  <td className="py-3"><OutcomeBadge outcome={c.outcome} /></td>
                  <td className="py-3">{c.analysis.overallScore}</td>
                  <td className="py-3">{c.analysis.speakingRateWpm}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-3xl border border-dashed border-[var(--accent)]/40 bg-[var(--accent)]/5 p-5">
        <SectionTitle
          title={`${INDUSTRY_PLAYBOOKS.length} playbook ngành sẵn sàng`}
          subtitle="Mở đầu · từ chối · chốt · từ khóa · ngữ điệu · tốc độ"
        />
        <Link href="/playbooks" className="inline-flex rounded-full bg-[var(--ink)] px-5 py-2.5 text-sm text-white">
          Xem kịch bản theo ngành
        </Link>
      </section>
    </div>
  );
}
