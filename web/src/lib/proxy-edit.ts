import type { ProxyDetail, ProxyKind, ProxyUpdate, RotationMethod } from "./types";
import {
  MAX_NOTE_LENGTH,
  charCount,
  concurrencyError,
  credentialError,
  durationError,
  durationSeconds,
  normalizePool,
  parseWholeNumber,
  poolError,
  rotationUrlError,
  toDurationValue,
  type DurationValue,
} from "./validation";

export interface ProxyEditValues {
  kind: ProxyKind;
  pool: string;
  maxConcurrency: string;
  note: string;
  username: string;
  /** Mật khẩu mới; để trống là giữ mật khẩu đang lưu (máy chủ không bao giờ trả mật khẩu về). */
  password: string;
  clearPassword: boolean;
  rotationUrl: string;
  rotationMethod: RotationMethod;
  rotationInterval: DurationValue;
  rotationCooldown: DurationValue;
  rotateOnBlock: boolean;
}

export type ProxyEditErrors = Partial<Record<keyof ProxyEditValues, string>>;

export function editValuesOf(proxy: ProxyDetail): ProxyEditValues {
  return {
    kind: proxy.kind,
    pool: proxy.pool,
    maxConcurrency: String(proxy.max_concurrency),
    note: proxy.note ?? "",
    username: proxy.username,
    password: "",
    clearPassword: false,
    rotationUrl: proxy.rotation_url_full ?? "",
    rotationMethod: proxy.rotation_method,
    rotationInterval: toDurationValue(proxy.rotation_interval_sec),
    rotationCooldown: toDurationValue(proxy.rotation_cooldown_sec),
    rotateOnBlock: proxy.rotate_on_block,
  };
}

export function noteLength(note: string): number {
  return charCount(note.trim());
}

function keepsPassword(values: ProxyEditValues, hadPassword: boolean): boolean {
  return values.password !== "" || (hadPassword && !values.clearPassword);
}

// Link cũ không giải mã được vẫn hiện nguyên văn trong ô nhập: chỉ kiểm tra khi người dùng sửa link.
function rotationUrlChanged(values: ProxyEditValues, initial: ProxyEditValues): boolean {
  return values.rotationUrl.trim() !== initial.rotationUrl.trim();
}

export function validateEdit(values: ProxyEditValues, initial: ProxyEditValues, hadPassword: boolean): ProxyEditErrors {
  const errors: ProxyEditErrors = {};
  const pool = poolError(values.pool);
  if (pool) errors.pool = pool;
  const concurrency = concurrencyError(values.maxConcurrency);
  if (concurrency) errors.maxConcurrency = concurrency;
  if (noteLength(values.note) > MAX_NOTE_LENGTH) errors.note = `Ghi chú tối đa ${MAX_NOTE_LENGTH} ký tự`;

  const username = values.username.trim();
  const usernameError = credentialError(username, "Tên đăng nhập", { allowColon: false });
  if (usernameError) {
    errors.username = usernameError;
  } else if (!username && keepsPassword(values, hadPassword)) {
    errors.username = "Proxy có mật khẩu thì phải có tên đăng nhập";
  }
  if (values.password) {
    const passwordError = credentialError(values.password, "Mật khẩu", { allowColon: true });
    if (passwordError) errors.password = passwordError;
  }

  if (values.kind === "rotating") {
    const urlError = rotationUrlChanged(values, initial) ? rotationUrlError(values.rotationUrl) : null;
    if (urlError) errors.rotationUrl = urlError;
    const interval = durationError(values.rotationInterval);
    if (interval) errors.rotationInterval = interval;
    const cooldown = durationError(values.rotationCooldown);
    if (cooldown) errors.rotationCooldown = cooldown;
  }
  return errors;
}

/** Chỉ gửi những trường người dùng đã đổi so với lúc mở form. */
export function buildEditPatch(values: ProxyEditValues, initial: ProxyEditValues, hadPassword: boolean): ProxyUpdate {
  const patch: ProxyUpdate = {};
  if (values.kind !== initial.kind) patch.kind = values.kind;
  const pool = normalizePool(values.pool);
  if (pool !== normalizePool(initial.pool)) patch.pool = pool;
  const maxConcurrency = parseWholeNumber(values.maxConcurrency);
  if (maxConcurrency !== null && maxConcurrency !== parseWholeNumber(initial.maxConcurrency)) {
    patch.max_concurrency = maxConcurrency;
  }
  const note = values.note.trim();
  if (note !== initial.note.trim()) patch.note = note;

  const username = values.username.trim();
  if (username !== initial.username.trim()) patch.username = username;
  if (values.password !== "") {
    patch.password = values.password;
  } else if (values.clearPassword && hadPassword) {
    patch.password = "";
  }

  if (values.kind === "rotating") {
    if (rotationUrlChanged(values, initial)) patch.rotation_url = values.rotationUrl.trim();
    if (values.rotationMethod !== initial.rotationMethod) patch.rotation_method = values.rotationMethod;
    const interval = durationSeconds(values.rotationInterval);
    if (interval !== null && interval !== durationSeconds(initial.rotationInterval)) {
      patch.rotation_interval_sec = interval;
    }
    const cooldown = durationSeconds(values.rotationCooldown);
    if (cooldown !== null && cooldown !== durationSeconds(initial.rotationCooldown)) {
      patch.rotation_cooldown_sec = cooldown;
    }
    if (values.rotateOnBlock !== initial.rotateOnBlock) patch.rotate_on_block = values.rotateOnBlock;
  }
  return patch;
}

/** Đổi tên đăng nhập hoặc mật khẩu thì máy chủ đưa proxy về "chưa kiểm tra" và phải kiểm tra lại. */
export function credentialsChanged(patch: ProxyUpdate): boolean {
  return patch.username !== undefined || patch.password !== undefined;
}
