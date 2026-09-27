import type { LoginResult } from "./types";

const SESSION_KEY = "commentscope.session";

export const NETWORK_ERROR_MESSAGE =
  "Không kết nối được tới máy chủ. Kiểm tra mạng hoặc máy chủ có đang chạy không.";

export interface StoredSession {
  token: string;
  username: string;
  expiresAt: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfterSec: number | null;

  constructor(message: string, status: number, retryAfterSec: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfterSec = retryAfterSec;
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Đã có lỗi không xác định";
}

const unauthorizedListeners = new Set<() => void>();

/** Được gọi khi máy chủ từ chối token đang lưu (hết hạn, đổi SECRET_KEY...). */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

export function loadSession(now: number = Date.now()): StoredSession | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
  if (!raw) {
    return null;
  }
  try {
    const value = JSON.parse(raw) as Partial<StoredSession>;
    if (
      typeof value.token === "string" &&
      typeof value.username === "string" &&
      typeof value.expiresAt === "string" &&
      Date.parse(value.expiresAt) > now
    ) {
      return { token: value.token, username: value.username, expiresAt: value.expiresAt };
    }
  } catch {
    // Dữ liệu hỏng: xoá bên dưới.
  }
  clearSession();
  return null;
}

export function saveSession(result: LoginResult): StoredSession {
  const session = { token: result.access_token, username: result.username, expiresAt: result.expires_at };
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // Trình duyệt chặn lưu trữ: phiên chỉ còn trong bộ nhớ của tab.
  }
  return session;
}

export function clearSession(): void {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    // Không có localStorage thì cũng không có gì để xoá.
  }
}

export type QueryValue = string | number | boolean | null | undefined;

export function buildUrl(path: string, query?: object): string {
  const params = new URLSearchParams();
  if (query) {
    for (const [key, value] of Object.entries(query) as [string, QueryValue][]) {
      if (value === undefined || value === null || value === "") {
        continue;
      }
      params.set(key, String(value));
    }
  }
  const search = params.toString();
  return search ? `${path}?${search}` : path;
}

export function filenameFromDisposition(header: string | null): string | null {
  if (!header) {
    return null;
  }
  const encoded = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(header);
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ""));
    } catch {
      // Tên file mã hoá sai: thử dạng filename= thường.
    }
  }
  const plain = /filename\s*=\s*("([^"]*)"|[^;]+)/i.exec(header);
  const name = plain?.[2] ?? plain?.[1]?.trim();
  return name === undefined || name === "" ? null : name;
}

async function toApiError(response: Response): Promise<ApiError> {
  let message = `Máy chủ trả về lỗi HTTP ${response.status}`;
  if (response.status === 502 || response.status === 503 || response.status === 504) {
    message = `Máy chủ đang khởi động lại hoặc không phản hồi (HTTP ${response.status}), hãy thử lại sau ít phút`;
  }
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === "string" && data.detail) {
      message = data.detail;
    }
  } catch {
    // Không phải JSON (ví dụ trang lỗi của reverse proxy).
  }
  const retryAfter = Number(response.headers.get("Retry-After"));
  return new ApiError(message, response.status, Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null);
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  query?: object;
  signal?: AbortSignal;
  auth?: boolean;
}

async function send(path: string, { method = "GET", body, query, signal, auth = true }: RequestOptions): Promise<Response> {
  const session = auth ? loadSession() : null;
  const headers = new Headers({ Accept: "application/json" });
  if (session) {
    headers.set("Authorization", `Bearer ${session.token}`);
  }
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (signal?.aborted) {
      throw error;
    }
    throw new ApiError(NETWORK_ERROR_MESSAGE, 0);
  }
  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401 && session) {
      clearSession();
      for (const listener of unauthorizedListeners) {
        listener();
      }
    }
    throw error;
  }
  return response;
}

async function request<T>(path: string, options: RequestOptions): Promise<T> {
  const response = await send(path, options);
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

type CallOptions = Omit<RequestOptions, "method" | "body">;

export const api = {
  get: <T>(path: string, options: CallOptions = {}) => request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options: CallOptions = {}) =>
    request<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body: unknown, options: CallOptions = {}) =>
    request<T>(path, { ...options, method: "PATCH", body }),
  delete: (path: string, options: CallOptions = {}) => request<undefined>(path, { ...options, method: "DELETE" }),
  async download(path: string, query?: object): Promise<{ blob: Blob; filename: string | null }> {
    const response = await send(path, { query });
    return { blob: await response.blob(), filename: filenameFromDisposition(response.headers.get("Content-Disposition")) };
  },
};
