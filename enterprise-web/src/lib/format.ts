export function formatVnd(amount: number | null | undefined): string {
  if (amount == null || Number.isNaN(amount)) return "—";
  return new Intl.NumberFormat("vi-VN", {
    style: "currency",
    currency: "VND",
    maximumFractionDigits: 0,
  }).format(amount);
}

export function formatNumber(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("vi-VN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(n);
}

/** Accepts 0–1 ratios (or already 0–100). */
export function formatPercentDisplay(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const value = Math.abs(n) <= 1 ? n * 100 : n;
  return `${formatNumber(value, 1)}%`;
}

export function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function formatTs(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  const ms = Math.round((sec % 1) * 10);
  return `${m}:${s.toString().padStart(2, "0")}.${ms}`;
}

export function formatDateTime(iso: string): string {
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(iso));
}

export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium" }).format(
    new Date(iso)
  );
}

export const STAGE_LABELS: Record<string, string> = {
  opening: "Mở đầu",
  discovery: "Khai thác",
  pitch: "Giới thiệu",
  objection: "Xử lý từ chối",
  close: "Chốt đơn",
  outro: "Kết thúc",
};

export const VERDICT_LABELS: Record<string, string> = {
  pass: "Đạt",
  fail: "Không đạt",
  not_applicable: "Không áp dụng",
  "Insufficient Evidence": "Thiếu bằng chứng",
  needs_review: "Cần review",
};

export const SEVERITY_LABELS: Record<string, string> = {
  critical: "Nghiêm trọng",
  major: "Lớn",
  minor: "Nhỏ",
  info: "Thông tin",
};

export function scoreTone(score: number | null | undefined): string {
  if (score == null) return "text-amber-400";
  if (score >= 80) return "text-emerald-400";
  if (score >= 60) return "text-sky-400";
  return "text-rose-400";
}

export function scoreFill(score: number | null | undefined): string {
  if (score == null) return "#f59e0b";
  if (score >= 80) return "#34d399";
  if (score >= 60) return "#38bdf8";
  return "#fb7185";
}
