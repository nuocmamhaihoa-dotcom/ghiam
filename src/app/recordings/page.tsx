"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Metric, OutcomeBadge, SectionTitle } from "@/components/ui";

type RecordingMeta = {
  id: string;
  title: string;
  agentName: string;
  phoneMasked: string;
  createdAt: number;
  hasAudio: boolean;
  hasTranscript: boolean;
  durationSec: number;
  grade: string | null;
  overallScore: number | null;
  outcome: string;
  readinessScore: number;
};

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

export default function RecordingsPage() {
  const [items, setItems] = useState<RecordingMeta[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const res = await fetch("/api/recordings?limit=100", { cache: "no-store" });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Load failed");
    setItems(data.items || []);
    setStats(data.stats || null);
  }, []);

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : "Lỗi tải"));
  }, [load]);

  async function syncNow() {
    setBusy(true);
    setError("");
    setMsg("Đang đồng bộ + phân tích sâu…");
    try {
      const res = await fetch("/api/recordings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "sync", limit: 20, downloadAudio: true }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Sync failed");
      setStats(data.stats || null);
      await load();
      const r = data.result;
      setMsg(
        `Import +${r.imported}, cập nhật ${r.updated}, audio ${r.withAudio}, bỏ qua ${r.skipped}` +
          (r.errors?.length ? ` · lỗi ${r.errors.length}` : ""),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync error");
    } finally {
      setBusy(false);
    }
  }

  async function reanalyzeNow() {
    setBusy(true);
    setError("");
    setMsg("Đang phân tích các cuộc gọi chưa chấm / schema cũ…");
    try {
      const res = await fetch("/api/recordings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "analyze-pending", limit: 200 }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Reanalyze failed");
      setStats(data.stats || null);
      await load();
      const r = data.result || {};
      setMsg(
        `Đã chấm lại ${r.updated ?? 0} cuộc · sẵn sàng tái tạo ${r.readyForRecreation ?? 0} · TB ${r.avgReadiness ?? 0}`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reanalyze error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl">
            Thư viện ghi âm
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">
            Lưu toàn bộ file ghi âm đã tải, phân tích sâu chỉ số chốt sale và
            dựng kịch bản tái tạo cuộc gọi.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void syncNow()}
            className="rounded-md bg-[var(--accent)] px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {busy ? "Đang xử lý…" : "Đồng bộ + phân tích"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void reanalyzeNow()}
            className="rounded-md border border-[var(--line)] bg-[var(--panel)] px-4 py-2 text-sm disabled:opacity-50"
          >
            Chấm lại readiness
          </button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Tổng ghi âm" value={String(stats?.total ?? 0)} />
        <Metric label="Có audio" value={String(stats?.withAudio ?? 0)} />
        <Metric
          label="Sẵn sàng tái tạo"
          value={String(stats?.readyForRecreation ?? 0)}
          hint={`TB readiness ${stats?.avgReadiness ?? 0}`}
        />
        <Metric
          label="Chờ phân tích"
          value={String(stats?.pendingAnalysis ?? 0)}
          hint={`schema v${stats?.analysisVersion ?? "-"}`}
        />
        <Metric label="Đã chốt" value={String(stats?.won ?? 0)} />
      </div>

      {msg ? <p className="text-sm text-[var(--muted)]">{msg}</p> : null}
      {error ? <p className="text-sm text-rose-700">{error}</p> : null}

      <section>
        <SectionTitle title="Danh sách đã lưu" subtitle="Sắp xếp mới nhất trước" />
        <div className="overflow-x-auto rounded-xl border border-[var(--line)]">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-[var(--chip)] text-[var(--muted)]">
              <tr>
                <th className="px-3 py-2">Cuộc gọi</th>
                <th className="px-3 py-2">TVV</th>
                <th className="px-3 py-2">Kết quả</th>
                <th className="px-3 py-2">Điểm</th>
                <th className="px-3 py-2">Readiness</th>
                <th className="px-3 py-2">Audio</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-[var(--muted)]">
                    Chưa có ghi âm local — bấm Đồng bộ + phân tích.
                  </td>
                </tr>
              ) : (
                items.map((item) => (
                  <tr key={item.id} className="border-t border-[var(--line)]">
                    <td className="px-3 py-2">
                      <Link
                        href={`/recordings/${item.id}`}
                        className="font-medium text-[var(--accent)] hover:underline"
                      >
                        {item.title}
                      </Link>
                      <div className="text-xs text-[var(--muted)]">
                        {item.phoneMasked} ·{" "}
                        {new Date(item.createdAt).toLocaleString("vi-VN")}
                      </div>
                    </td>
                    <td className="px-3 py-2">{item.agentName}</td>
                    <td className="px-3 py-2">
                      <OutcomeBadge outcome={item.outcome} />
                    </td>
                    <td className="px-3 py-2">
                      {item.grade || "—"} / {item.overallScore ?? "—"}
                    </td>
                    <td className="px-3 py-2 font-medium">
                      {item.readinessScore}
                    </td>
                    <td className="px-3 py-2">
                      {item.hasAudio ? "Có" : "Chưa"}
                      {item.hasTranscript ? " · STT" : ""}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
