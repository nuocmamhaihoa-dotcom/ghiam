"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Metric, OutcomeBadge, ScoreRing, SectionTitle } from "@/components/ui";
import { INDUSTRY_PLAYBOOKS } from "@/lib/analyzeCall";
import { CHOTKIEM_LIVE_STATS } from "@/lib/chotKiemSeed";
import {
  getInsights,
  loadCalls,
  mergeChotKiemSamples,
  resetDemoCalls,
  type StoredCall,
} from "@/lib/store";

type LiveStats = {
  totals: {
    calls: number;
    avgScore: number;
    completeCalls: number;
    uniquePhones: number;
    agents: number;
  };
  criteria: {
    passRate: number;
    avgScore: number;
    coreFailFrequency: Array<{ key: string; label: string; failCount: number }>;
  };
  fetchedAt?: string;
};

type ItyStatus = {
  settings?: { enabledAccounts?: string[]; accountCount?: number };
  download?: { pendingCount?: number; serverCanDownload?: boolean };
  pending?: { count?: number };
};

type ItyProgress = {
  phase: "idle" | "metadata" | "downloads" | "done" | "error";
  round: number;
  discovered: number;
  imported: number;
  skipped: number;
  remoteTotal: number;
  lastPage: number;
  pendingCount: number;
  message: string;
};

export default function HomePage() {
  const [calls, setCalls] = useState<StoredCall[]>([]);
  const [live, setLive] = useState<LiveStats | null>(null);
  const [syncMsg, setSyncMsg] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [ityBusy, setItyBusy] = useState(false);
  const [ityStatus, setItyStatus] = useState<ItyStatus | null>(null);
  const [ityProgress, setItyProgress] = useState<ItyProgress>({
    phase: "idle",
    round: 0,
    discovered: 0,
    imported: 0,
    skipped: 0,
    remoteTotal: 0,
    lastPage: 0,
    pendingCount: 0,
    message: "",
  });

  useEffect(() => {
    setCalls(loadCalls());
    fetch("/api/chotkiem")
      .then((r) => r.json())
      .then((d) => {
        if (d.ok) setLive(d.stats);
      })
      .catch(() => undefined);
    fetch("/api/ity")
      .then((r) => r.json())
      .then((d) => {
        if (d.ok) setItyStatus(d);
      })
      .catch(() => undefined);
  }, []);

  const insights = getInsights(calls);
  const liveTotals = live?.totals;
  const liveGaps =
    live?.criteria.coreFailFrequency?.length
      ? live.criteria.coreFailFrequency
      : CHOTKIEM_LIVE_STATS.topGaps.map((g) => ({
          key: g.key,
          label: g.label,
          failCount: g.failCount,
        }));

  async function syncFromChotKiem() {
    setSyncing(true);
    setSyncMsg("");
    try {
      const res = await fetch("/api/chotkiem", { method: "POST" });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Sync failed");
      if (data.stats) setLive(data.stats);
      const next = mergeChotKiemSamples(data.samples || []);
      setCalls(next);
      setSyncMsg(
        `Đã kéo ${data.imported} cuộc từ ChốtKiểm (kho ${data.stats?.totals?.calls ?? "?"} cuộc).`,
      );
    } catch (e) {
      setSyncMsg(e instanceof Error ? e.message : "Không sync được ChốtKiểm");
    } finally {
      setSyncing(false);
    }
  }

  /** Pull all ITY recordings for the last 10 days via ChốtKiểm (paginated). */
  async function syncItyLast10Days() {
    if (ityBusy) return;
    setItyBusy(true);
    setItyProgress({
      phase: "metadata",
      round: 0,
      discovered: 0,
      imported: 0,
      skipped: 0,
      remoteTotal: 0,
      lastPage: 0,
      pendingCount: ityStatus?.download?.pendingCount ?? 0,
      message: "Đang quét metadata ITY 10 ngày gần nhất…",
    });

    try {
      let startPage = 1;
      let discovered = 0;
      let imported = 0;
      let skipped = 0;
      let remoteTotal = 0;
      let lastPage = 0;
      let round = 0;

      // Client-driven rounds keep each request under gateway timeouts.
      for (let i = 0; i < 80; i += 1) {
        round = i + 1;
        const res = await fetch("/api/ity", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "sync-round",
            daysBack: 10,
            startPage,
            maxPages: 1,
            pageLimit: 100,
            autoAnalyze: false,
          }),
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || "ITY sync-round failed");

        const r = data.round || {};
        discovered += Number(r.discovered || 0);
        imported += Number(r.imported || 0);
        skipped += Number(r.skipped || 0);
        remoteTotal = Math.max(remoteTotal, Number(r.remoteTotal || 0));
        lastPage = Number(r.lastPage || startPage);

        setItyProgress({
          phase: "metadata",
          round,
          discovered,
          imported,
          skipped,
          remoteTotal,
          lastPage,
          pendingCount: Number(r.pendingBrowserDownload || 0),
          message: `Lô ${round}: trang ${lastPage}/${remoteTotal ? Math.ceil(remoteTotal / 100) : "?"} · nhập ${imported} · bỏ qua ${skipped}`,
        });

        if (!r.hasMore || !data.nextStartPage) break;
        startPage = Number(data.nextStartPage);
      }

      setItyProgress((p) => ({
        ...p,
        phase: "downloads",
        message: "Metadata xong — kích tải ghi âm nền + xử lý hàng chờ…",
      }));

      const dlRes = await fetch("/api/ity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "start-downloads",
          batchSize: 5,
          autoAnalyze: false,
        }),
      });
      const dlData = await dlRes.json();
      if (!dlData.ok) throw new Error(dlData.error || "ITY start-downloads failed");

      // A few more pending batches; long downloads continue on ChốtKiểm via drain/proxy-hunt.
      let pendingCount = Number(dlData.pendingCount ?? 0);
      for (let i = 0; i < 3; i += 1) {
        const batch = await fetch("/api/ity", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "process-pending",
            batchSize: 3,
            autoAnalyze: false,
          }),
        });
        const batchData = await batch.json();
        if (batchData.pendingCount != null) pendingCount = Number(batchData.pendingCount);
        setItyProgress((p) => ({
          ...p,
          pendingCount,
          message: batchData.timedOut
            ? `Tải ghi âm chậm/timeout — còn ~${pendingCount} pending (drain nền vẫn chạy)`
            : `Đã kích batch tải · còn ~${pendingCount} pending`,
        }));
        if (!pendingCount) break;
      }

      const statusRes = await fetch("/api/ity");
      const statusData = await statusRes.json();
      if (statusData.ok) setItyStatus(statusData);
      pendingCount = Number(
        statusData.download?.pendingCount ?? statusData.pending?.count ?? pendingCount,
      );

      setItyProgress((p) => ({
        ...p,
        phase: "done",
        pendingCount,
        message: `Xong quét 10 ngày: phát hiện ${p.discovered}, nhập mới ${p.imported}, bỏ qua ${p.skipped}. Pending ghi âm còn ~${pendingCount} (ChốtKiểm tải nền).`,
      }));
    } catch (e) {
      setItyProgress((p) => ({
        ...p,
        phase: "error",
        message: e instanceof Error ? e.message : "Lỗi sync ITY",
      }));
    } finally {
      setItyBusy(false);
    }
  }

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
            Đã nối ChốtKiểm (`222.255.215.55`) — rubric QA 9 tiêu chí + sync mẫu cuộc gọi live.
            Độ gần coach người ~75–85% trên transcript.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              href="/analyze"
              className="rounded-full bg-[var(--accent)] px-5 py-2.5 text-sm font-medium text-white"
            >
              Phân tích cuộc gọi mới
            </Link>
            <button
              type="button"
              onClick={() => void syncFromChotKiem()}
              disabled={syncing || ityBusy}
              className="rounded-full border border-[var(--accent)] bg-[var(--accent)]/10 px-5 py-2.5 text-sm text-[var(--accent)] disabled:opacity-60"
            >
              {syncing ? "Đang sync…" : "Sync từ ChốtKiểm"}
            </button>
            <button
              type="button"
              onClick={() => void syncItyLast10Days()}
              disabled={ityBusy || syncing}
              className="rounded-full border border-[var(--line)] bg-[var(--ink)] px-5 py-2.5 text-sm text-white disabled:opacity-60"
            >
              {ityBusy ? "Đang tải ITY…" : "Tải ITY 10 ngày"}
            </button>
            <button
              type="button"
              onClick={() => setCalls(resetDemoCalls())}
              className="rounded-full border border-[var(--line)] bg-[var(--panel)] px-5 py-2.5 text-sm"
            >
              Reset data demo
            </button>
          </div>
          {syncMsg ? <p className="mt-3 text-sm text-[var(--muted)]">{syncMsg}</p> : null}
          {ityProgress.phase !== "idle" ? (
            <div className="mt-4 rounded-2xl border border-[var(--line)] bg-[var(--panel)]/80 p-4 text-sm">
              <div className="font-medium">
                ITY 10 ngày ·{" "}
                {ityProgress.phase === "metadata"
                  ? "quét metadata"
                  : ityProgress.phase === "downloads"
                    ? "tải ghi âm"
                    : ityProgress.phase === "done"
                      ? "hoàn tất"
                      : "lỗi"}
              </div>
              <p className="mt-1 text-[var(--muted)]">{ityProgress.message}</p>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                <div>
                  <div className="text-[var(--muted)]">Phát hiện</div>
                  <div className="text-base font-medium">{ityProgress.discovered}</div>
                </div>
                <div>
                  <div className="text-[var(--muted)]">Nhập mới</div>
                  <div className="text-base font-medium">{ityProgress.imported}</div>
                </div>
                <div>
                  <div className="text-[var(--muted)]">Remote total</div>
                  <div className="text-base font-medium">{ityProgress.remoteTotal}</div>
                </div>
                <div>
                  <div className="text-[var(--muted)]">Pending audio</div>
                  <div className="text-base font-medium">
                    {ityProgress.pendingCount ||
                      ityStatus?.download?.pendingCount ||
                      "—"}
                  </div>
                </div>
              </div>
              {ityStatus?.settings?.enabledAccounts?.length ? (
                <p className="mt-2 text-xs text-[var(--muted)]">
                  Tài khoản ITY: {ityStatus.settings.enabledAccounts.join(", ")}
                </p>
              ) : null}
            </div>
          ) : ityStatus?.download ? (
            <p className="mt-3 text-xs text-[var(--muted)]">
              ITY sẵn sàng · pending ghi âm ~{ityStatus.download.pendingCount ?? "?"}
              {ityStatus.settings?.enabledAccounts?.length
                ? ` · TK ${ityStatus.settings.enabledAccounts.join(", ")}`
                : ""}
            </p>
          ) : null}
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-6">
          <div className="mb-3 flex items-center justify-between gap-3">
            <SectionTitle
              title="ChốtKiểm live"
              subtitle={
                live?.fetchedAt
                  ? `Cập nhật ${new Date(live.fetchedAt).toLocaleString("vi-VN")}`
                  : "Snapshot / API"
              }
            />
            <ScoreRing
              score={Math.round(liveTotals?.avgScore ?? CHOTKIEM_LIVE_STATS.avgScore)}
              label="điểm TB"
            />
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <div className="text-[var(--muted)]">Kho ghi âm</div>
              <div className="font-[family-name:var(--font-display)] text-xl">
                {liveTotals?.calls ?? CHOTKIEM_LIVE_STATS.calls}
              </div>
            </div>
            <div>
              <div className="text-[var(--muted)]">Pass rate</div>
              <div className="font-[family-name:var(--font-display)] text-xl">
                {live?.criteria.passRate ?? CHOTKIEM_LIVE_STATS.passRate}%
              </div>
            </div>
            <div>
              <div className="text-[var(--muted)]">Đủ tiêu chí</div>
              <div className="font-[family-name:var(--font-display)] text-xl">
                {liveTotals?.completeCalls ?? CHOTKIEM_LIVE_STATS.completeCalls}
              </div>
            </div>
            <div>
              <div className="text-[var(--muted)]">Sale / agent</div>
              <div className="font-[family-name:var(--font-display)] text-xl">
                {liveTotals?.agents ?? CHOTKIEM_LIVE_STATS.agents}
              </div>
            </div>
          </div>
          <p className="mt-4 text-xs text-[var(--muted)]">{insights.realismNote}</p>
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
            subtitle="Gap từ kho live 222.255.215.55 (+ phân tích local)"
          />
          <div className="space-y-3">
            {liveGaps.slice(0, 6).map((g) => (
              <div
                key={g.key || g.label}
                className="flex items-center justify-between rounded-xl bg-[var(--chip)]/70 px-3 py-2"
              >
                <div className="text-sm font-medium">{g.label}</div>
                <div className="text-sm text-[var(--bad)]">{g.failCount} cuộc thiếu</div>
              </div>
            ))}
            {insights.criteriaGaps.length > 0 ? (
              <p className="text-xs text-[var(--muted)]">
                Local: {insights.criteriaGaps.map((g) => `${g.label}×${g.count}`).join(" · ")}
              </p>
            ) : null}
          </div>
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle
            title="Từ khóa khiến khách đồng ý"
            subtitle="Tỉ lệ xuất hiện trong cuộc thắng"
          />
          <div className="space-y-3">
            {insights.topKeywords.map((k) => (
              <div key={k.keyword} className="flex items-center gap-3">
                <div className="w-28 text-sm font-medium">{k.keyword}</div>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-[var(--chip)]">
                  <div
                    className="h-full rounded-full bg-[var(--accent)]"
                    style={{ width: `${k.lift}%` }}
                  />
                </div>
                <div className="w-20 text-right text-sm text-[var(--muted)]">
                  {k.lift}% · {k.count}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle title="Theo ngành hàng" />
          <div className="space-y-3">
            {insights.industries.map((row) => (
              <div
                key={row.industry}
                className="flex items-center justify-between rounded-xl bg-[var(--chip)]/70 px-3 py-2"
              >
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
              <p key={i} className="prose-quote rounded-r-xl py-2 text-sm">
                {s}
              </p>
            ))}
          </div>
        </div>
        <div className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <SectionTitle title="Cách chốt đơn hiệu quả" />
          <div className="space-y-3">
            {insights.bestCloses.map((s, i) => (
              <p key={i} className="prose-quote rounded-r-xl py-2 text-sm">
                {s}
              </p>
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
          <Link href="/calls" className="text-sm text-[var(--accent)]">
            Xem tất cả
          </Link>
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
              {calls.slice(0, 8).map((c) => (
                <tr key={c.id} className="border-t border-[var(--line)]">
                  <td className="py-3">
                    <Link href={`/calls/${c.id}`} className="font-medium hover:text-[var(--accent)]">
                      {c.title}
                    </Link>
                    <div className="text-xs text-[var(--muted)]">
                      {c.agentName}
                      {c.source === "chotkiem" ? " · ChốtKiểm" : ""}
                    </div>
                  </td>
                  <td className="py-3">{c.industry}</td>
                  <td className="py-3">
                    <OutcomeBadge outcome={c.outcome} />
                  </td>
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
        <Link
          href="/playbooks"
          className="inline-flex rounded-full bg-[var(--ink)] px-5 py-2.5 text-sm text-white"
        >
          Xem kịch bản theo ngành
        </Link>
      </section>
    </div>
  );
}
