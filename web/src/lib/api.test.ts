import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  NETWORK_ERROR_MESSAGE,
  api,
  buildUrl,
  clearSession,
  filenameFromDisposition,
  loadSession,
  onUnauthorized,
  saveSession,
} from "./api";
import type { LoginResult } from "./types";

const SESSION_KEY = "commentscope.session";

const LOGIN: LoginResult = {
  access_token: "tok-123",
  token_type: "bearer",
  expires_at: "2099-01-01T00:00:00Z",
  username: "admin",
};

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
}

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), { status: 200, ...init });
}

let storage: ReturnType<typeof memoryStorage>;
let fetchMock: Mock<typeof fetch>;

beforeEach(() => {
  storage = memoryStorage();
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("localStorage", storage);
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastRequest(): { url: string; init: RequestInit | undefined; headers: Headers } {
  const call = fetchMock.mock.lastCall;
  if (call === undefined) throw new Error("fetch chưa được gọi");
  const [input, init] = call;
  if (typeof input !== "string") throw new Error("client phải gọi fetch bằng URL dạng chuỗi");
  return { url: input, init, headers: new Headers(init?.headers) };
}

describe("buildUrl", () => {
  it("bỏ tham số rỗng, giữ false và 0", () => {
    expect(buildUrl("/api/proxies")).toBe("/api/proxies");
    expect(buildUrl("/api/proxies", { page: 2, q: "a b", enabled: false, retries: 0, pool: "", kind: undefined, x: null })).toBe(
      "/api/proxies?page=2&q=a+b&enabled=false&retries=0",
    );
  });
});

describe("filenameFromDisposition", () => {
  it.each([
    [null, null],
    ["attachment", null],
    ['attachment; filename=""', null],
    ['attachment; filename="proxies-20260927.txt"', "proxies-20260927.txt"],
    ["attachment; filename=proxies.txt", "proxies.txt"],
    ["attachment; filename*=UTF-8''danh%20s%C3%A1ch.txt", "danh sách.txt"],
    ["attachment; filename*=UTF-8''%E0%A4%A; filename=\"du-phong.txt\"", "du-phong.txt"],
  ])("%j → %j", (header, name) => {
    expect(filenameFromDisposition(header)).toBe(name);
  });
});

describe("phiên đăng nhập", () => {
  it("lưu, đọc lại và xoá", () => {
    saveSession(LOGIN);
    expect(loadSession()).toEqual({ token: "tok-123", username: "admin", expiresAt: "2099-01-01T00:00:00Z" });
    clearSession();
    expect(loadSession()).toBeNull();
  });

  it("bỏ phiên đã hết hạn", () => {
    saveSession({ ...LOGIN, expires_at: "2026-01-01T00:00:00Z" });
    expect(loadSession(Date.parse("2026-01-01T00:00:01Z"))).toBeNull();
    expect(storage.getItem(SESSION_KEY)).toBeNull();
  });

  it.each(["{không phải json", JSON.stringify({ token: 1, username: "admin", expiresAt: "2099-01-01" })])(
    "bỏ dữ liệu hỏng %j",
    (raw) => {
      storage.setItem(SESSION_KEY, raw);
      expect(loadSession()).toBeNull();
      expect(storage.getItem(SESSION_KEY)).toBeNull();
    },
  );
});

describe("gọi API", () => {
  it("gửi token đăng nhập và trả về JSON", async () => {
    saveSession(LOGIN);
    fetchMock.mockResolvedValue(jsonResponse({ total: 3 }));
    await expect(api.get("/api/proxies/stats")).resolves.toEqual({ total: 3 });
    const { url, init, headers } = lastRequest();
    expect(url).toBe("/api/proxies/stats");
    expect(init?.method).toBe("GET");
    expect(headers.get("Authorization")).toBe("Bearer tok-123");
  });

  it("gửi body dạng JSON", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ matched: 1 }));
    await api.post("/api/proxies/bulk", { action: "check", ids: [1] });
    const { init, headers } = lastRequest();
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"action":"check","ids":[1]}');
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.has("Authorization")).toBe(false);
  });

  it("204 không có nội dung", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.delete("/api/proxies/1")).resolves.toBeUndefined();
  });

  it("dùng thông báo lỗi của máy chủ", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Đã có proxy cùng host, cổng và tên đăng nhập" }, { status: 409 }));
    await expect(api.patch("/api/proxies/1", { username: "a" })).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      message: "Đã có proxy cùng host, cổng và tên đăng nhập",
      retryAfterSec: null,
    });
  });

  it("trang lỗi HTML của reverse proxy vẫn có thông báo dễ hiểu", async () => {
    fetchMock.mockResolvedValue(new Response("<html>Bad Gateway</html>", { status: 502 }));
    await expect(api.get("/api/proxies")).rejects.toMatchObject({
      status: 502,
      message: "Máy chủ đang khởi động lại hoặc không phản hồi (HTTP 502), hãy thử lại sau ít phút",
    });
  });

  it("đọc Retry-After khi bị giới hạn tốc độ", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "Đăng nhập sai quá nhiều lần" }, { status: 429, headers: { "Retry-After": "7" } }),
    );
    await expect(api.post("/api/auth/login", {}, { auth: false })).rejects.toMatchObject({
      status: 429,
      retryAfterSec: 7,
    });
  });

  it("mất mạng thì báo lỗi kết nối", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    await expect(api.get("/api/proxies")).rejects.toMatchObject({ status: 0, message: NETWORK_ERROR_MESSAGE });
  });

  it("huỷ yêu cầu thì ném lại đúng lỗi huỷ", async () => {
    const controller = new AbortController();
    controller.abort();
    const abortError = new DOMException("Đã huỷ", "AbortError");
    fetchMock.mockRejectedValue(abortError);
    await expect(api.get("/api/proxies", { signal: controller.signal })).rejects.toBe(abortError);
  });

  it("token bị từ chối thì xoá phiên và báo cho ứng dụng", async () => {
    saveSession(LOGIN);
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Phiên đăng nhập đã hết hạn" }, { status: 401 }));
    await expect(api.get("/api/proxies")).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(loadSession()).toBeNull();
    unsubscribe();
  });

  it("sai mật khẩu khi đăng nhập không bị coi là hết phiên", async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Sai tên đăng nhập hoặc mật khẩu" }, { status: 401 }));
    await expect(api.post("/api/auth/login", {}, { auth: false })).rejects.toMatchObject({
      message: "Sai tên đăng nhập hoặc mật khẩu",
    });
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("tải file kèm tên file", async () => {
    fetchMock.mockResolvedValue(
      new Response("1.1.1.1:80\n", { headers: { "Content-Disposition": 'attachment; filename="proxies.txt"' } }),
    );
    const result = await api.download("/api/proxies/export", { format: "url", with_options: true });
    expect(result.filename).toBe("proxies.txt");
    expect(await result.blob.text()).toBe("1.1.1.1:80\n");
    expect(lastRequest().url).toBe("/api/proxies/export?format=url&with_options=true");
  });
});
