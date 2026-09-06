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

type Progress = {
  updatedAt: string;
  phase: string;
  message: string;
  pendingCount: number;
  livingCount: number;
  drainRunning: boolean;
  hunterRunning: boolean;
  filesPerMinute: number;
  lifetimeFilesPerMinute?: number;
  etaMinutes: number | null;
  completedSinceStart: number;
  windowMinutes: number;
  libraryTotal: number;
  libraryWithAudio: number;
  events: Array<{
    at: string;
    phase: string;
    message: string;
    pendingCount?: number;
    delta?: number;
  }>;
  samples: Array<{
    at: string;
    pendingCount: number;
    livingCount: number;
  }>;
};

function formatEta(minutes: number | null): string {
  if (minutes == null) return "—";
  if (minutes < 60) return `~${minutes} phút`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `~${h}g ${m}p`;
}

type AutoState = {
  enabled: boolean;
  tickCount: number;
  lastTickAt: string | null;
  lastError: string | null;
  lastSummary: string | null;
  consecutiveErrors: number;
  config?: { intervalSec?: number };
  stats?: {
    filesProcessed?: number;
    imported?: number;
    reanalyzed?: number;
  };
};

export default function ProxyPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [autoState, setAutoState] = useState<AutoState | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const refreshAuto = useCallback(async () => {
    try {
      const res = await fetch("/api/ity/auto", { cache: "no-store" });
      const data = await res.json();
      if (data.ok && data.state) setAutoState(data.state as AutoState);
    } catch {
      // non-fatal — board still works without auto status
    }
  }, []);

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
      if (data.progress) setProgress(data.progress);
      await refreshAuto();
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
  }, [refreshAuto]);

  const toggleAuto = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const enable = !autoState?.enabled;
      const res = await fetch("/api/ity/auto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: enable ? "enable" : "disable" }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || "Auto API error");
      if (data.state) setAutoState(data.state as AutoState);
      setMsg(
        enable
          ? data.result?.message || "Đã bật tự động tải + phân tích liên tục"
          : "Đã tắt pipeline tự động",
      );
      await refresh(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lỗi bật/tắt auto");
    } finally {
      setBusy(false);
    }
  }, [autoState?.enabled, refresh]);

  const runProgressAction = useCallback(
    async (action: "boost-downloads" | "import-library" | "sample") => {
      setBusy(true);
      setError("");
      try {
        const res = await fetch("/api/ity/progress", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            action === "boost-downloads"
              ? { action, rounds: 4, batchSize: 8, concurrency: 3 }
              : action === "import-library"
                ? { action, importLimit: 30 }
                : { action: "sample" },
          ),
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || "Progress API error");
        if (data.progress) setProgress(data.progress);
        if (action === "boost-downloads") {
          setMsg(
            `Boost tải: ${data.rounds ?? 0} lô · pending ~${data.pendingCount ?? "?"} · ${data.progress?.filesPerMinute ?? 0} file/phút`,
          );
        } else if (action === "import-library") {
          const r = data.result || {};
          setMsg(
            `Đã lưu thư viện: +${r.imported ?? 0} mới, ${r.updated ?? 0} cập nhật, audio ${r.withAudio ?? 0}`,
          );
        } else {
          setMsg(data.progress?.message || "Đã làm mới tốc độ");
        }
        await refresh(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Lỗi tiến độ");
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  useEffect(() => {
    void refresh(false);
    const timer = setInterval(() => void refresh(false), 15_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const living = snapshot?.livingCount ?? progress?.livingCount ?? 0;
  const minLiving = snapshot?.minLiving ?? 20;
  const pending =
    progress?.pendingCount ?? snapshot?.pendingDownloads ?? 0;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl">
            Proxy & tốc độ tải ghi âm
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">
            Giữ proxy sống, theo dõi tốc độ tải ITY, rồi lưu vĩnh viễn + phân tích
            vào thư viện ghi âm — không đổi các luồng sync/metadata đã ổn.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
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
            className="rounded-md border border-[var(--line)] px-3 py-2 text-sm disabled:opacity-50"
          >
            Săn proxy ngay
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void runProgressAction("boost-downloads")}
            className="rounded-md bg-[var(--accent)] px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            Tăng tốc tải
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void runProgressAction("import-library")}
            className="rounded-md border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm disabled:opacity-50"
          >
            Lưu & phân tích
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void toggleAuto()}
            className={`rounded-md px-3 py-2 text-sm disabled:opacity-50 ${
              autoState?.enabled
                ? "bg-emerald-700 text-white"
                : "border border-[var(--line)] bg-[var(--panel)]"
            }`}
          >
            {autoState?.enabled ? "Đang tự động · Tắt" : "Bật tự động liên tục"}
          </button>
        </div>
      </div>

      <section className="space-y-3 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-4">
        <SectionTitle
          title="Pipeline tự động"
          subtitle="Tải ITY → lưu thư viện → phân tích chuyên sâu, chạy liên tục qua worker"
        />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            label="Trạng thái"
            value={autoState?.enabled ? "BẬT" : "TẮT"}
            hint={`chu kỳ ${autoState?.config?.intervalSec ?? 45}s · tick #${autoState?.tickCount ?? 0}`}
          />
          <Metric
            label="Đã xử lý"
            value={String(autoState?.stats?.filesProcessed ?? 0)}
            hint="file qua process-pending"
          />
          <Metric
            label="Đã import"
            value={String(autoState?.stats?.imported ?? 0)}
            hint={`reanalyze ${autoState?.stats?.reanalyzed ?? 0}`}
          />
          <Metric
            label="Lần gần nhất"
            value={
              autoState?.lastTickAt
                ? new Date(autoState.lastTickAt).toLocaleTimeString("vi-VN")
                : "—"
            }
            hint={autoState?.lastError || autoState?.lastSummary || "Chưa chạy tick"}
          />
        </div>
        {autoState?.lastSummary ? (
          <p className="text-sm text-[var(--muted)]">{autoState.lastSummary}</p>
        ) : null}
      </section>

      <section className="space-y-4">
        <SectionTitle
          title="Bảng tốc độ & tiến độ"
          subtitle="Đo file/phút từ pending giảm theo thời gian · ETA ước tính"
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            label="Tốc độ tải"
            value={`${progress?.filesPerMinute ?? 0}`}
            hint={`file/phút · cửa sổ ${progress?.windowMinutes ?? 0}p · TB cả phiên ${progress?.lifetimeFilesPerMinute ?? 0}`}
          />
          <Metric
            label="Pending còn lại"
            value={String(pending)}
            hint={
              progress?.drainRunning || snapshot?.drainRunning
                ? "Drain đang chạy"
                : "Chờ drain / boost"
            }
          />
          <Metric
            label="ETA"
            value={formatEta(progress?.etaMinutes ?? null)}
            hint={`Đã xong ~${progress?.completedSinceStart ?? 0} file từ lúc theo dõi`}
          />
          <Metric
            label="Thư viện local"
            value={`${progress?.libraryWithAudio ?? 0}/${progress?.libraryTotal ?? 0}`}
            hint="Có audio / tổng đã lưu"
          />
        </div>
        <p className="rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-3 text-sm">
          <span className="font-medium">Pha: {progress?.phase || "idle"}</span>
          {" · "}
          {progress?.message || "Chưa có mẫu tốc độ — bấm Tăng tốc tải hoặc đợi drain."}
        </p>
      </section>

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
          value={String(snapshot?.pendingDownloads ?? pending)}
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

      {msg ? <p className="text-sm text-[var(--muted)]">{msg}</p> : null}
      {error ? <p className="text-sm text-rose-700">{error}</p> : null}
      {snapshot?.hint ? (
        <p className="rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-3 text-sm">
          {snapshot.hint}
        </p>
      ) : null}
      {snapshot?.lastError ? (
        <p className="text-sm text-rose-700">
          Lỗi gần nhất: {snapshot.lastError}
        </p>
      ) : null}

      <section>
        <SectionTitle
          title="Nhật ký công việc gần đây"
          subtitle="Boost tải / import thư viện / thay đổi pending"
        />
        <div className="overflow-x-auto rounded-xl border border-[var(--line)]">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-[var(--chip)] text-[var(--muted)]">
              <tr>
                <th className="px-3 py-2 font-medium">Thời điểm</th>
                <th className="px-3 py-2 font-medium">Pha</th>
                <th className="px-3 py-2 font-medium">Pending</th>
                <th className="px-3 py-2 font-medium">Δ</th>
                <th className="px-3 py-2 font-medium">Chi tiết</th>
              </tr>
            </thead>
            <tbody>
              {(progress?.events || []).length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="px-3 py-3 text-[var(--muted)]"
                  >
                    Chưa có sự kiện — bấm Tăng tốc tải để bắt đầu.
                  </td>
                </tr>
              ) : (
                [...(progress?.events || [])]
                  .reverse()
                  .slice(0, 25)
                  .map((ev) => (
                    <tr key={`${ev.at}-${ev.message}`} className="border-t border-[var(--line)]">
                      <td className="px-3 py-2 whitespace-nowrap">
                        {new Date(ev.at).toLocaleString("vi-VN")}
                      </td>
                      <td className="px-3 py-2">{ev.phase}</td>
                      <td className="px-3 py-2">{ev.pendingCount ?? "—"}</td>
                      <td className="px-3 py-2">
                        {ev.delta != null && ev.delta !== 0
                          ? ev.delta > 0
                            ? `−${ev.delta}`
                            : `+${Math.abs(ev.delta)}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2">{ev.message}</td>
                    </tr>
                  ))
              )}
            </tbody>
          </table>
        </div>
      </section>

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
