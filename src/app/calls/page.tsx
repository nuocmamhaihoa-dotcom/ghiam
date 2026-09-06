"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { OutcomeBadge, SectionTitle } from "@/components/ui";
import { deleteCall, loadCalls, type StoredCall } from "@/lib/store";

export default function CallsPage() {
  const [calls, setCalls] = useState<StoredCall[]>([]);
  useEffect(() => setCalls(loadCalls()), []);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <SectionTitle
          title="Kho cuộc gọi"
          subtitle="Opening · objection · closing · tốc độ nói · từ khóa"
        />
        <Link href="/analyze" className="rounded-full bg-[var(--accent)] px-4 py-2 text-sm text-white">
          + Thêm cuộc gọi
        </Link>
      </div>

      <div className="grid gap-4">
        {calls.map((c) => (
          <div key={c.id} className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <Link href={`/calls/${c.id}`} className="font-[family-name:var(--font-display)] text-xl hover:text-[var(--accent)]">
                  {c.title}
                </Link>
                <div className="mt-1 text-sm text-[var(--muted)]">
                  {c.industry} · {c.product} · {c.agentName} · {c.durationSec}s
                </div>
              </div>
              <div className="flex items-center gap-2">
                <OutcomeBadge outcome={c.outcome} />
                <span className="rounded-full bg-[var(--chip)] px-2.5 py-1 text-xs">
                  Score {c.analysis.overallScore}
                </span>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-sm md:grid-cols-5">
              <div>Opening: {c.analysis.openingScore}</div>
              <div>Closing: {c.analysis.closingScore}</div>
              <div>WPM: {c.analysis.speakingRateWpm}</div>
              <div>Tone: {c.analysis.toneLabel}</div>
              <div>KW: {c.analysis.keywordsHit.slice(0, 2).join(", ") || "—"}</div>
            </div>
            <div className="mt-3 flex gap-3">
              <Link href={`/calls/${c.id}`} className="text-sm text-[var(--accent)]">Chi tiết</Link>
              <button
                type="button"
                className="text-sm text-[var(--bad)]"
                onClick={() => {
                  deleteCall(c.id);
                  setCalls(loadCalls());
                }}
              >
                Xóa
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
