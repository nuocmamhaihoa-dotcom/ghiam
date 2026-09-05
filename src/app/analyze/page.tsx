"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Metric, SectionTitle } from "@/components/ui";

type Stats = {
  total: number;
  withAudio: number;
  withTranscript: number;
  readyForRecreation: number;
  pendingAnalysis: number;
  analysisVersion: number;
  avgReadiness: number;
  won: number;
};

type RunResult = {
  updated?: number;
  scanned?: number;
  pendingLeft?: number;
  readyForRecreation?: number;
  avgReadiness?: number;
  analysisVersion?: number;
};

export default function AnalyzePage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [lastResult, setLastResult] = useState<RunResult | null>(null);
  const [autoLoop, setAutoLoop] = useState(false);

  const load = useCallback(async () => {
    const res = await fetch("/api/recordings?limit=1", { cache: "no-store" });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Không tải được kho ghi âm");
    setStats(data.stats || null);
  }, []);

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : "Lỗi tải"));
  }, [load]);

  const runAnalyze = useCallback(
    async (mode: "pending" | "force") => {
      setBusy(true);
      setError("");
      setMsg(
        mode === "pending"
          ? "Đang phân tích các cuộc gọi chưa chấm / schema cũ…"
          : "Đang chấm lại toàn bộ kho với bộ tiêu chí mở rộng…",
      );
      try {
        const res = await fetch("/api/recordings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: mode === "pending" ? "analyze-pending" : "reanalyze",
            limit: 200,
            force: mode === "force",
          }),
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || "Phân tích thất bại");
        setStats(data.stats || null);
        setLastResult(data.result || null);
        const r = data.result || {};
        setMsg(
          `Đã phân tích ${r.updated ?? 0}/${r.scanned ?? 0} cuộc gọi · còn chờ ${r.pendingLeft ?? data.stats?.pendingAnalysis ?? 0} · sẵn sàng tái tạo ${r.readyForRecreation ?? data.stats?.readyForRecreation ?? 0}`,
        );
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Lỗi phân tích");
        setAutoLoop(false);
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  useEffect(() => {
    if (!autoLoop || busy) return;
    if ((stats?.pendingAnalysis ?? 0) <= 0) {
      setAutoLoop(false);
      setMsg((m) => m || "Hết cuộc gọi chờ phân tích — dừng vòng lặp tự động.");
      return;
    }
    const t = setTimeout(() => {
      void runAnalyze("pending");
    }, 1500);
    return () => clearTimeout(t);
  }, [autoLoop, busy, stats?.pendingAnalysis, runAnalyze]);

  return (
    <div className="space-y-6">
      <SectionTitle
        title="Phân tích cuộc gọi tự động"
        subtitle="Chấm toàn bộ kho ghi âm chưa phân tích: tiêu chí chất lượng ChốtKiểm + coaching, và gói dữ liệu để AI tái tạo cuộc gọi."
      />

      <div className="grid gap-4 md:grid-cols-4">
        <Metric label="Tổng trong kho" value={`${stats?.total ?? "—"}`} />
        <Metric
          label="Chờ phân tích"
          value={`${stats?.pendingAnalysis ?? "—"}`}
          hint={`schema v${stats?.analysisVersion ?? "—"}`}
        />
        <Metric
          label="Sẵn sàng tái tạo AI"
          value={`${stats?.readyForRecreation ?? "—"}`}
        />
        <Metric label="Readiness TB" value={`${stats?.avgReadiness ?? "—"}`} />
      </div>

      <div className="space-y-4 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <p className="text-sm text-[var(--muted)]">
          Hệ thống lấy transcript trong Thư viện ghi âm, chấm 9 tiêu chí ChốtKiểm
          + chỉ số coaching (opening, discovery, pitch, objection, close,
          talk-balance…), rồi sinh gói AI Clone (slot biến, kịch bản thay biến,
          nhánh xử lý từ chối) để gọi lại khách khác.
        </p>

        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={() => void runAnalyze("pending")}
            className="rounded-full bg-[var(--accent)] px-5 py-2.5 text-sm font-medium text-white disabled:opacity-60"
          >
            {busy ? "Đang chạy…" : "Phân tích tất cả chưa chấm"}
          </button>
          <button
            type="button"
            disabled={busy || (stats?.pendingAnalysis ?? 0) <= 0}
            onClick={() => setAutoLoop(true)}
            className="rounded-full border border-[var(--line)] bg-white px-5 py-2.5 text-sm disabled:opacity-60"
          >
            {autoLoop ? "Đang lặp tự động…" : "Lặp đến hết hàng chờ"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setAutoLoop(false);
              void runAnalyze("force");
            }}
            className="rounded-full border border-[var(--line)] bg-white px-5 py-2.5 text-sm disabled:opacity-60"
          >
            Chấm lại toàn bộ (schema mới)
          </button>
          <Link
            href="/recordings"
            className="rounded-full border border-[var(--line)] px-5 py-2.5 text-sm text-[var(--accent)]"
          >
            Mở thư viện ghi âm
          </Link>
        </div>

        {msg ? <p className="text-sm text-emerald-700">{msg}</p> : null}
        {error ? <p className="text-sm text-rose-700">{error}</p> : null}

        {lastResult ? (
          <div className="grid gap-2 rounded-2xl bg-white/70 p-4 text-sm md:grid-cols-3">
            <div>Đã cập nhật: {lastResult.updated ?? 0}</div>
            <div>Đã quét: {lastResult.scanned ?? 0}</div>
            <div>Còn chờ: {lastResult.pendingLeft ?? "—"}</div>
          </div>
        ) : null}
      </div>

      <div className="space-y-3 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 text-sm">
        <h2 className="font-medium">Bộ tiêu chí mở rộng</h2>
        <ul className="list-disc space-y-1 pl-5 text-[var(--muted)]">
          <li>
            Chất lượng QA: chào hỏi, tên SP, số lượng, giá, địa chỉ, đồng ý /
            từ chối, thái độ KH & TVV (chuẩn ChốtKiểm).
          </li>
          <li>
            Coaching: opening, discovery, pitch, xử lý từ chối, chốt, cân bằng
            nói, lịch sự, đồng cảm.
          </li>
          <li>
            AI tái tạo: persona giọng nói, slot điền ([product], [price],
            [address]…), kịch bản thay biến, nhánh objection, câu mở/chốt.
          </li>
        </ul>
      </div>
    </div>
  );
}
