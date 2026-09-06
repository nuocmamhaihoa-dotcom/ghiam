"use client";

import { SectionTitle } from "@/components/ui";
import { INDUSTRY_PLAYBOOKS } from "@/lib/analyzeCall";

export default function PlaybooksPage() {
  return (
    <div className="space-y-6">
      <SectionTitle
        title="Kịch bản theo ngành hàng"
        subtitle="Opening · xử lý từ chối · chốt đơn · từ khóa · ngữ điệu · tốc độ nói"
      />
      <div className="space-y-5">
        {INDUSTRY_PLAYBOOKS.map((pb) => (
          <article key={pb.industry} className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
            <h2 className="font-[family-name:var(--font-display)] text-2xl">{pb.title}</h2>
            <p className="mt-1 text-sm text-[var(--muted)]">{pb.industry}</p>

            <div className="mt-5 grid gap-5 lg:grid-cols-2">
              <div>
                <h3 className="text-xs uppercase tracking-[0.14em] text-[var(--accent)]">Cách mở đầu</h3>
                <ul className="mt-2 space-y-2">
                  {pb.openingScripts.map((s, i) => (
                    <li key={i} className="prose-quote rounded-r-xl py-2 text-sm">{s}</li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="text-xs uppercase tracking-[0.14em] text-[var(--accent)]">Cách chốt đơn</h3>
                <ul className="mt-2 space-y-2">
                  {pb.closingScripts.map((s, i) => (
                    <li key={i} className="prose-quote rounded-r-xl py-2 text-sm">{s}</li>
                  ))}
                </ul>
              </div>
            </div>

            <div className="mt-5">
              <h3 className="text-xs uppercase tracking-[0.14em] text-[var(--accent-2)]">Xử lý từ chối</h3>
              <div className="mt-2 grid gap-3 md:grid-cols-2">
                {pb.objectionScripts.map((o, i) => (
                  <div key={i} className="rounded-2xl border border-[var(--line)] p-4">
                    <div className="text-sm font-medium">KH: {o.objection}</div>
                    <p className="mt-2 text-sm text-[var(--muted)]">Sale: {o.reply}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-5 grid gap-4 md:grid-cols-3">
              <div>
                <h3 className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">Từ khóa lực</h3>
                <p className="mt-2 text-sm">{pb.powerKeywords.join(" · ")}</p>
              </div>
              <div>
                <h3 className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">Ngữ điệu</h3>
                <p className="mt-2 text-sm">{pb.toneGuide}</p>
              </div>
              <div>
                <h3 className="text-xs uppercase tracking-[0.14em] text-[var(--muted)]">Tốc độ nói</h3>
                <p className="mt-2 text-sm">{pb.paceGuide}</p>
              </div>
            </div>
            <p className="mt-4 text-sm text-[var(--accent)]">{pb.winRateHint}</p>
          </article>
        ))}
      </div>
    </div>
  );
}
