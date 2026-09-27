import { describe, expect, it } from "vitest";

import {
  buildEditPatch,
  credentialsChanged,
  editValuesOf,
  noteLength,
  validateEdit,
  type ProxyEditValues,
} from "./proxy-edit";
import type { ProxyDetail } from "./types";

const UNDECRYPTABLE_URL = "(không giải mã được, hãy nhập lại link)";

function makeProxy(overrides: Partial<ProxyDetail> = {}): ProxyDetail {
  return {
    id: 7,
    kind: "rotating",
    protocol: "http",
    host: "4g.nhacungcap.vn",
    port: 10001,
    username: "user",
    has_password: true,
    display: "http://user:***@4g.nhacungcap.vn:10001",
    pool: "default",
    note: null,
    enabled: true,
    max_concurrency: 1,
    active_leases: 0,
    rotation_mode: "url",
    rotation_url: "https://nhacungcap.vn/api/change-ip?key=***",
    rotation_method: "GET",
    rotation_interval_sec: 600,
    rotation_cooldown_sec: 90,
    rotate_on_block: true,
    rotation_state: "idle",
    cooldown_remaining_sec: 0,
    last_rotated_at: null,
    last_rotation_attempt_at: null,
    last_rotation_ok: null,
    last_rotation_message: null,
    rotation_count: 0,
    health: "alive",
    check_in_progress: false,
    last_checked_at: null,
    last_check_error: null,
    latency_ms: null,
    exit_ip: null,
    country: null,
    isp: null,
    success_count: 0,
    failure_count: 0,
    consecutive_failures: 0,
    quarantined_until: null,
    last_used_at: null,
    created_at: "2026-09-27T10:00:00Z",
    updated_at: "2026-09-27T10:00:00Z",
    rotation_url_full: "https://nhacungcap.vn/api/change-ip?key=abc123",
    ...overrides,
  };
}

const initial = editValuesOf(makeProxy());

function edit(change: Partial<ProxyEditValues>): ProxyEditValues {
  return { ...initial, ...change };
}

describe("editValuesOf", () => {
  it("điền form từ proxy, ô mật khẩu luôn trống", () => {
    expect(initial).toEqual({
      kind: "rotating",
      pool: "default",
      maxConcurrency: "1",
      note: "",
      username: "user",
      password: "",
      clearPassword: false,
      rotationUrl: "https://nhacungcap.vn/api/change-ip?key=abc123",
      rotationMethod: "GET",
      rotationInterval: { amount: "10", unit: "m" },
      rotationCooldown: { amount: "90", unit: "s" },
      rotateOnBlock: true,
    });
  });
});

describe("buildEditPatch", () => {
  it("không đổi gì thì không gửi gì", () => {
    expect(buildEditPatch(initial, initial, true)).toEqual({});
    expect(buildEditPatch(edit({ rotationInterval: { amount: "600", unit: "s" } }), initial, true)).toEqual({});
  });

  it("chỉ gửi trường đã đổi, đã chuẩn hoá", () => {
    const values = edit({ pool: "  vn-hcm ", maxConcurrency: " 3 ", note: "  4G Viettel  " });
    expect(buildEditPatch(values, initial, true)).toEqual({ pool: "vn-hcm", max_concurrency: 3, note: "4G Viettel" });
  });

  it("mật khẩu: để trống là giữ, nhập là đổi, tích xoá là gửi chuỗi rỗng", () => {
    expect(buildEditPatch(edit({ password: "" }), initial, true)).toEqual({});
    expect(buildEditPatch(edit({ password: "p@ss:1" }), initial, true)).toEqual({ password: "p@ss:1" });
    expect(buildEditPatch(edit({ clearPassword: true }), initial, true)).toEqual({ password: "" });
    expect(buildEditPatch(edit({ clearPassword: true }), initial, false)).toEqual({});
  });

  it("proxy 4G gửi thiết lập đổi IP đã đổi", () => {
    const values = edit({
      rotationUrl: " https://nhacungcap.vn/api/v2/change-ip?key=xyz ",
      rotationMethod: "POST",
      rotationInterval: { amount: "1", unit: "h" },
      rotationCooldown: { amount: "2", unit: "m" },
      rotateOnBlock: false,
    });
    expect(buildEditPatch(values, initial, true)).toEqual({
      rotation_url: "https://nhacungcap.vn/api/v2/change-ip?key=xyz",
      rotation_method: "POST",
      rotation_interval_sec: 3600,
      rotation_cooldown_sec: 120,
      rotate_on_block: false,
    });
    expect(buildEditPatch(edit({ rotationUrl: "" }), initial, true)).toEqual({ rotation_url: "" });
  });

  it("chuyển sang proxy tĩnh thì không gửi thiết lập đổi IP", () => {
    const values = edit({ kind: "static", rotationUrl: "", rotationMethod: "POST" });
    expect(buildEditPatch(values, initial, true)).toEqual({ kind: "static" });
  });

  it("đổi tên đăng nhập hoặc mật khẩu thì phải kiểm tra lại proxy", () => {
    expect(credentialsChanged(buildEditPatch(edit({ username: " user2 " }), initial, true))).toBe(true);
    expect(credentialsChanged(buildEditPatch(edit({ clearPassword: true }), initial, true))).toBe(true);
    expect(credentialsChanged(buildEditPatch(edit({ pool: "vn" }), initial, true))).toBe(false);
  });
});

describe("validateEdit", () => {
  it("form chưa sửa không có lỗi", () => {
    expect(validateEdit(initial, initial, true)).toEqual({});
  });

  it("báo lỗi pool, số máy và ghi chú quá dài", () => {
    const errors = validateEdit(edit({ pool: "vn hcm", maxConcurrency: "0", note: "a".repeat(501) }), initial, true);
    expect(errors).toEqual({
      pool: "Tên pool chỉ gồm chữ, số và các ký tự _ . - (không có dấu cách)",
      maxConcurrency: "Nhập số từ 1 đến 100",
      note: "Ghi chú tối đa 500 ký tự",
    });
  });

  it("đếm ghi chú theo code point và bỏ khoảng trắng hai đầu như máy chủ", () => {
    expect(noteLength("  👍👍  ")).toBe(2);
    expect(validateEdit(edit({ note: "👍".repeat(500) }), initial, true)).toEqual({});
  });

  it("proxy có mật khẩu thì phải có tên đăng nhập", () => {
    const missing = "Proxy có mật khẩu thì phải có tên đăng nhập";
    expect(validateEdit(edit({ username: " " }), initial, true)).toEqual({ username: missing });
    expect(validateEdit(edit({ username: "", clearPassword: true }), initial, true)).toEqual({});
    expect(validateEdit(edit({ username: "" }), initial, false)).toEqual({});
    expect(validateEdit(edit({ username: "", password: "secret" }), initial, false)).toEqual({ username: missing });
    expect(validateEdit(edit({ username: "a:b" }), initial, true)).toEqual({
      username: "Tên đăng nhập không được chứa dấu ':'",
    });
    expect(validateEdit(edit({ password: "có dấu cách" }), initial, true)).toEqual({
      password: "Mật khẩu không được chứa dấu cách hoặc ký tự |",
    });
  });

  it("chỉ kiểm tra link đổi IP khi người dùng sửa link", () => {
    const broken = editValuesOf(makeProxy({ rotation_url_full: UNDECRYPTABLE_URL }));
    expect(validateEdit(broken, broken, true)).toEqual({});
    expect(buildEditPatch(broken, broken, true)).toEqual({});
    expect(validateEdit({ ...broken, rotationUrl: "ftp://x" }, broken, true)).toEqual({
      rotationUrl: "Link đổi IP phải bắt đầu bằng http:// hoặc https://",
    });
  });

  it("thiết lập đổi IP sai chỉ tính khi là proxy 4G", () => {
    const values = edit({
      rotationUrl: "không phải link",
      rotationInterval: { amount: "25", unit: "h" },
      rotationCooldown: { amount: "", unit: "s" },
    });
    expect(validateEdit(values, initial, true)).toEqual({
      rotationUrl: "Link đổi IP không hợp lệ",
      rotationInterval: "Tối đa 24 giờ",
      rotationCooldown: "Nhập số nguyên không âm, ví dụ 0, 30, 10",
    });
    expect(validateEdit({ ...values, kind: "static" }, initial, true)).toEqual({});
  });
});
