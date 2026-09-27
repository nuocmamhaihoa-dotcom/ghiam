export const MAX_DURATION_SEC = 86_400;
export const MAX_CONCURRENCY = 100;
export const MAX_NOTE_LENGTH = 500;
export const MAX_CREDENTIAL_LENGTH = 255;
export const MAX_ROTATION_URL_LENGTH = 2000;
export const MAX_POOL_LENGTH = 64;

// Khớp `^[\w.\-]+$` của máy chủ: `\w` bên Python nhận cả chữ có dấu tiếng Việt.
const POOL_RE = /^[\p{L}\p{N}_.-]+$/u;
const CREDENTIAL_FORBIDDEN_RE = /[\s|]/;
const WHOLE_NUMBER_RE = /^\d{1,9}$/;

/** Đếm ký tự như máy chủ (Python `len`): theo code point, nên emoji chỉ tính là 1 ký tự. */
export function charCount(text: string): number {
  return Array.from(text).length;
}

export function normalizePool(value: string): string {
  return value.trim().normalize("NFC");
}

export function poolError(value: string): string | null {
  const pool = normalizePool(value);
  if (!pool) {
    return "Hãy nhập tên pool";
  }
  if (charCount(pool) > MAX_POOL_LENGTH) {
    return `Tên pool tối đa ${MAX_POOL_LENGTH} ký tự`;
  }
  if (!POOL_RE.test(pool)) {
    return "Tên pool chỉ gồm chữ, số và các ký tự _ . - (không có dấu cách)";
  }
  return null;
}

export function parseWholeNumber(text: string): number | null {
  const trimmed = text.trim();
  return WHOLE_NUMBER_RE.test(trimmed) ? Number(trimmed) : null;
}

export function concurrencyError(text: string): string | null {
  const value = parseWholeNumber(text);
  return value === null || value < 1 || value > MAX_CONCURRENCY ? `Nhập số từ 1 đến ${MAX_CONCURRENCY}` : null;
}

export type DurationUnit = "s" | "m" | "h";

export const DURATION_UNITS: readonly DurationUnit[] = ["s", "m", "h"];
export const DURATION_UNIT_LABELS: Record<DurationUnit, string> = { s: "giây", m: "phút", h: "giờ" };
const UNIT_SECONDS: Record<DurationUnit, number> = { s: 1, m: 60, h: 3600 };

/** Giá trị ô nhập thời lượng: số (dạng chữ để gõ dở vẫn giữ được) và đơn vị. */
export interface DurationValue {
  amount: string;
  unit: DurationUnit;
}

/** Chọn đơn vị lớn nhất chia hết: 600 → 10 phút, 7200 → 2 giờ, 90 → 90 giây. */
export function toDurationValue(totalSec: number): DurationValue {
  if (totalSec >= 3600 && totalSec % 3600 === 0) {
    return { amount: String(totalSec / 3600), unit: "h" };
  }
  if (totalSec >= 60 && totalSec % 60 === 0) {
    return { amount: String(totalSec / 60), unit: "m" };
  }
  return { amount: String(totalSec), unit: "s" };
}

export function durationSeconds(value: DurationValue): number | null {
  const amount = parseWholeNumber(value.amount);
  if (amount === null) {
    return null;
  }
  const seconds = amount * UNIT_SECONDS[value.unit];
  return seconds <= MAX_DURATION_SEC ? seconds : null;
}

export function durationError(value: DurationValue): string | null {
  if (parseWholeNumber(value.amount) === null) {
    return "Nhập số nguyên không âm, ví dụ 0, 30, 10";
  }
  return durationSeconds(value) === null ? "Tối đa 24 giờ" : null;
}

export function credentialError(value: string, label: string, { allowColon }: { allowColon: boolean }): string | null {
  if (CREDENTIAL_FORBIDDEN_RE.test(value)) {
    return `${label} không được chứa dấu cách hoặc ký tự |`;
  }
  if (!allowColon && value.includes(":")) {
    return `${label} không được chứa dấu ':'`;
  }
  if (charCount(value) > MAX_CREDENTIAL_LENGTH) {
    return `${label} tối đa ${MAX_CREDENTIAL_LENGTH} ký tự`;
  }
  return null;
}

export function rotationUrlError(value: string): string | null {
  const url = value.trim();
  if (!url) {
    return null;
  }
  if (charCount(url) > MAX_ROTATION_URL_LENGTH) {
    return `Link đổi IP tối đa ${MAX_ROTATION_URL_LENGTH} ký tự`;
  }
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return "Link đổi IP không hợp lệ";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return "Link đổi IP phải bắt đầu bằng http:// hoặc https://";
  }
  return null;
}
