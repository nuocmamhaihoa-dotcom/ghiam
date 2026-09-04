"use client";

import { useCallback, useEffect, useState } from "react";
import { Metric, SectionTitle } from "@/components/ui";

type Snapshot = {
  livingCount: number;
  poolSize: number;
  configuredCount: number;
  deadCount: number;
  minLiving: number;
  hunterRunning: boolean;
  hunterEnabled: boolean;
  nightWindowActive: boolean;
  nightWindowLabel: string | null;
  drainRunning: boolean;
  drainEnabled: boolean;
  pendingDownloads: number;
  lastError: string | null;
  lastReason: string | null;
  hint: string | null;
  sampleProxies: string[];
  fetchedAt: string;
  autoEnsured: boolean;
  ensureAction: "none" | "hunted" | "skipped";
  lastSourcesOk?: number;
  lastSourcesTried?: number;
  lastCandidates?: number;
};

type HistoryPoint = {
  at: string;
  livingCount: number;
  poolSize: number;
  pendingDownloads: number;
};

export default function ProxyPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async (forceHunt = false) => {
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/proxy", {
        method: forceHunt ? "POST" : "GET",
        headers: forceHunt ? { "Content-Type": "application/json" } : undefined,
        body: forceHunt ? JSON.stringify({ forceHunt: true }) : undefined,
        cache: "no-store",
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Proxy API error");
      setSnapshot(data.snapshot);
      if (Array.isArray(data.history)) setHistory(data.history);
      setMsg(
        forceHunt
          ? `Đã kích săn proxy · living=${data.snapshot?.livingCount ?? "?"}`
          : `Auto-ensure: ${data.snapshot?.ensureAction || "none"} · living=${data.snapshot?.livingCount ?? "?"}`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lỗi proxy");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh(false);
    const timer = setInterval(() => void refresh(false), 60_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const living = snapshot?.livingCount ?? 0;
  const minLiving = snapshot?.minLiving ?? 20;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl">
            Proxy tải ghi âm
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">
            Tự động quét và giữ proxy sống để tải file ghi âm ITY. Hệ thống săn
            lại khi living &lt; ngưỡng hoặc còn pending download.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void refresh(false)}
            className="rounded-md border border-[var(--line)] px-3 py-2 text-sm disabled:opacity-50"
          >
            Làm mới
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void refresh(true)}
            className="rounded-md bg-[var(--accent)] px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            Săn proxy ngay
          </button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Proxy còn sống"
          value={String(living)}
          hint={`Ngưỡng tối thiểu: ${minLiving}`}
        />
        <Metric
          label="Pool / cấu hình"
          value={`${snapshot?.poolSize ?? 0}/${snapshot?.configuredCount ?? 0}`}
          hint={`Chết: ${snapshot?.deadCount ?? 0}`}
        />
        <Metric
          label="Pending tải về"
          value={String(snapshot?.pendingDownloads ?? 0)}
          hint={
            snapshot?.drainRunning
              ? "Drain đang chạy"
              : snapshot?.drainEnabled
                ? "Drain sẵn sàng"
                : "Drain tắt"
          }
        />
        <Metric
          label="Hunter"
          value={snapshot?.hunterRunning ? "Đang săn" : "Idle"}
          hint={
            snapshot?.nightWindowActive
              ? snapshot.nightWindowLabel || "Cửa sổ đêm"
              : "Ngoài cửa sổ đêm"
          }
        />
      </div>

      {msg ? (
        <p className="text-sm text-[var(--muted)]">{msg}</p>
      ) : null}
      {error ? <p className="text-sm text-rose-700">{error}</p> : null}
      {snapshot?.hint ? (
        <p className="rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-3 text-sm">
          {snapshot.hint}
        </p>
      ) : null}
      {snapshot?.lastError ? (
        <p className="text-sm text-rose-700">Lỗi gần nhất: {snapshot.lastError}</p>
      ) : null}

      <section>
        <SectionTitle
          title="Proxy mẫu đang sống"
          subtitle="Đã ẩn credential — dùng để tải ghi âm"
        />
        <div className="flex flex-wrap gap-2">
          {(snapshot?.sampleProxies || []).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">Chưa có proxy living.</p>
          ) : (
            snapshot?.sampleProxies.map((p) => (
              <code
                key={p}
                className="rounded-md bg-[var(--chip)] px-2 py-1 text-xs"
              >
                {p}
              </code>
            ))
          )}
        </div>
      </section>

      <section>
        <SectionTitle
          title="Lịch sử living proxy"
          subtitle="Mỗi lần auto-ensure / làm mới"
        />
        <div className="overflow-x-auto rounded-xl border border-[var(--line)]">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-[var(--chip)] text-[var(--muted)]">
              <tr>
                <th className="px-3 py-2 font-medium">Thời điểm</th>
                <th className="px-3 py-2 font-medium">Living</th>
                <th className="px-3 py-2 font-medium">Pool</th>
                <th className="px-3 py-2 font-medium">Pending</th>
              </tr>
            </thead>
            <tbody>
              {[...history].reverse().slice(0, 30).map((h) => (
                <tr key={h.at} className="border-t border-[var(--line)]">
                  <td className="px-3 py-2">
                    {new Date(h.at).toLocaleString("vi-VN")}
                  </td>
                  <td className="px-3 py-2 font-medium">{h.livingCount}</td>
                  <td className="px-3 py-2">{h.poolSize}</td>
                  <td className="px-3 py-2">{h.pendingDownloads}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
