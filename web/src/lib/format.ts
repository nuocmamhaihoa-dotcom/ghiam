import type {
  PreviewStatus,
  ProxyHealth,
  ProxyItem,
  ProxyKind,
  ProxyProtocol,
  ProxySort,
  RotationMode,
  RotationState,
} from "./types";

export const KIND_LABELS: Record<ProxyKind, string> = { static: "Tĩnh", rotating: "4G xoay" };

export const PROTOCOL_LABELS: Record<ProxyProtocol, string> = { http: "HTTP", https: "HTTPS", socks5: "SOCKS5" };

export const HEALTH_LABELS: Record<ProxyHealth, string> = {
  alive: "Sống",
  dead: "Chết",
  unchecked: "Chưa kiểm tra",
};

export const ROTATION_MODE_LABELS: Record<RotationMode, string> = {
  none: "Không đổi IP được",
  url: "Gọi link đổi IP",
  session: "Đổi session",
  provider: "Nhà cung cấp tự đổi",
};

export const ROTATION_STATE_LABELS: Record<RotationState, string> = {
  idle: "Sẵn sàng",
  pending: "Chờ đổi IP",
  rotating: "Đang đổi IP",
};

export const PREVIEW_STATUS_LABELS: Record<PreviewStatus, string> = {
  new: "Mới",
  update: "Cập nhật",
  duplicate: "Trùng",
  invalid: "Lỗi",
};

export const SORT_LABELS: Record<ProxySort, string> = {
  "-id": "Mới thêm trước",
  id: "Thêm trước",
  latency: "Nhanh nhất",
  "-latency": "Chậm nhất",
  "-last_checked": "Mới kiểm tra",
  last_checked: "Lâu chưa kiểm tra",
  last_used: "Dùng gần đây",
  host: "Theo host (A→Z)",
};

const numberFormat = new Intl.NumberFormat("vi-VN");
const decimalFormat = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 });
const dateTimeFormat = new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "medium" });

export function formatNumber(value: number): string {
  return numberFormat.format(value);
}

/** 90 → "1 phút 30 giây", 3660 → "1 giờ 1 phút" (bỏ giây khi đã tính tới giờ). */
export function formatDuration(totalSec: number): string {
  const total = Math.max(0, Math.round(totalSec));
  if (total === 0) {
    return "0 giây";
  }
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts: string[] = [];
  if (days) parts.push(`${days} ngày`);
  if (hours) parts.push(`${hours} giờ`);
  if (minutes && !days) parts.push(`${minutes} phút`);
  if (seconds && !days && !hours) parts.push(`${seconds} giây`);
  return parts.join(" ");
}

export function formatLatency(ms: number | null): string {
  if (ms === null) {
    return "—";
  }
  return ms < 1000 ? `${ms} ms` : `${decimalFormat.format(ms / 1000)} s`;
}

/** Thời điểm tương đối so với `now` (ms): "vừa xong", "5 phút trước", "sau 2 giờ". */
export function formatRelative(iso: string | null, now: number): string {
  if (!iso) {
    return "—";
  }
  const diffSec = Math.round((Date.parse(iso) - now) / 1000);
  const abs = Math.abs(diffSec);
  if (abs < 10) {
    return "vừa xong";
  }
  let text: string;
  if (abs < 60) text = `${abs} giây`;
  else if (abs < 3600) text = `${Math.floor(abs / 60)} phút`;
  else if (abs < 86_400) text = `${Math.floor(abs / 3600)} giờ`;
  else text = `${Math.floor(abs / 86_400)} ngày`;
  return diffSec < 0 ? `${text} trước` : `sau ${text}`;
}

/** Thời gian còn lại tới `iso`, hoặc null nếu đã qua. */
export function formatRemaining(iso: string | null, now: number): string | null {
  if (!iso) {
    return null;
  }
  const sec = Math.ceil((Date.parse(iso) - now) / 1000);
  return sec > 0 ? formatDuration(sec) : null;
}

export function formatDateTime(iso: string | null): string {
  return iso ? dateTimeFormat.format(new Date(iso)) : "—";
}

export function isFuture(iso: string | null, now: number): boolean {
  return iso !== null && Date.parse(iso) > now;
}

type CooldownFields = Pick<ProxyItem, "cooldown_remaining_sec" | "last_rotation_attempt_at" | "rotation_cooldown_sec">;

/**
 * Số giây còn phải chờ trước lần đổi IP kế tiếp, đếm lùi theo đồng hồ trình duyệt giữa hai lần tải dữ liệu.
 * Không bao giờ vượt con số máy chủ trả về, nên đồng hồ máy lệch cũng không làm thời gian chờ dài ra.
 */
export function cooldownLeft(proxy: CooldownFields, now: number): number {
  if (proxy.cooldown_remaining_sec <= 0 || !proxy.last_rotation_attempt_at) {
    return 0;
  }
  const elapsedSec = (now - Date.parse(proxy.last_rotation_attempt_at)) / 1000;
  const left = Math.ceil(proxy.rotation_cooldown_sec - elapsedSec);
  return Math.max(0, Math.min(proxy.cooldown_remaining_sec, left));
}

type LeaseFields = Pick<
  ProxyItem,
  "enabled" | "health" | "rotation_state" | "quarantined_until" | "active_leases" | "max_concurrency"
>;

/** Máy PC có thuê được proxy này lúc này không, theo đúng các điều kiện máy chủ xét khi cho thuê. */
export function leaseAvailability(proxy: LeaseFields, now: number): { available: boolean; text: string } {
  if (!proxy.enabled) {
    return { available: false, text: "Không, proxy đang tắt" };
  }
  if (proxy.health === "dead") {
    return { available: false, text: "Không, proxy đang chết" };
  }
  if (proxy.health === "unchecked") {
    return { available: false, text: "Chưa, proxy chưa được kiểm tra sống/chết" };
  }
  if (proxy.rotation_state === "rotating") {
    return { available: false, text: "Tạm dừng trong lúc đổi IP" };
  }
  if (proxy.rotation_state === "pending") {
    return { available: false, text: "Tạm dừng, chờ máy đang dùng trả proxy để đổi IP" };
  }
  const quarantine = formatRemaining(proxy.quarantined_until, now);
  if (quarantine) {
    return { available: false, text: `Tạm dừng, đang bị cách ly thêm ${quarantine}` };
  }
  const free = proxy.max_concurrency - proxy.active_leases;
  if (free <= 0) {
    return { available: false, text: `Đã đủ ${proxy.max_concurrency} máy dùng cùng lúc` };
  }
  return { available: true, text: `Sẵn sàng, còn ${free} chỗ trống` };
}

type RotationFields = Pick<ProxyItem, "kind" | "rotation_mode" | "rotation_interval_sec">;

/** Mô tả ngắn cách proxy đổi IP, ví dụ "Gọi link đổi IP · tự đổi mỗi 10 phút". */
export function describeRotation(proxy: RotationFields): string {
  if (proxy.kind === "static") {
    return "IP cố định";
  }
  const interval = proxy.rotation_interval_sec;
  switch (proxy.rotation_mode) {
    case "none":
      return "Chưa có link đổi IP hoặc {session}";
    case "provider":
      return `Nhà cung cấp tự đổi mỗi ${formatDuration(interval)}`;
    case "url":
    case "session":
      return interval > 0
        ? `${ROTATION_MODE_LABELS[proxy.rotation_mode]} · tự đổi mỗi ${formatDuration(interval)}`
        : ROTATION_MODE_LABELS[proxy.rotation_mode];
  }
}
