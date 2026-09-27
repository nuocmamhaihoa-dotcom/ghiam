import { describe, expect, it } from "vitest";

import {
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
} from "./validation";

describe("charCount", () => {
  it("đếm theo code point như Python", () => {
    expect(charCount("abc")).toBe(3);
    expect(charCount("đ")).toBe(1);
    expect(charCount("👍")).toBe(1);
  });
});

describe("pool", () => {
  it("chuẩn hoá khoảng trắng và dạng Unicode", () => {
    expect(normalizePool("  cafe\u0301 ")).toBe("caf\u00e9");
  });

  it.each([
    ["", "Hãy nhập tên pool"],
    ["   ", "Hãy nhập tên pool"],
    ["vn hcm", "Tên pool chỉ gồm chữ, số và các ký tự _ . - (không có dấu cách)"],
    ["a|b", "Tên pool chỉ gồm chữ, số và các ký tự _ . - (không có dấu cách)"],
    ["a".repeat(65), "Tên pool tối đa 64 ký tự"],
  ])("%j → %s", (value, error) => {
    expect(poolError(value)).toBe(error);
  });

  it.each(["default", "  vn-hcm ", "hà-nội", "viettel_4g.01", "a".repeat(64)])("%j hợp lệ", (value) => {
    expect(poolError(value)).toBeNull();
  });
});

describe("số nguyên và số máy dùng cùng lúc", () => {
  it("chỉ nhận số nguyên không âm", () => {
    expect(parseWholeNumber(" 42 ")).toBe(42);
    expect(parseWholeNumber("4.2")).toBeNull();
    expect(parseWholeNumber("-1")).toBeNull();
    expect(parseWholeNumber("")).toBeNull();
    expect(parseWholeNumber("1234567890")).toBeNull();
  });

  it("giới hạn từ 1 đến 100", () => {
    expect(concurrencyError("1")).toBeNull();
    expect(concurrencyError("100")).toBeNull();
    for (const value of ["0", "101", "abc", ""]) {
      expect(concurrencyError(value)).toBe("Nhập số từ 1 đến 100");
    }
  });
});

describe("thời lượng", () => {
  it.each([
    [0, { amount: "0", unit: "s" }],
    [90, { amount: "90", unit: "s" }],
    [600, { amount: "10", unit: "m" }],
    [5400, { amount: "90", unit: "m" }],
    [7200, { amount: "2", unit: "h" }],
  ] as const)("%s giây → %o", (seconds, value) => {
    expect(toDurationValue(seconds)).toEqual(value);
  });

  it("đổi ra giây, tối đa 24 giờ", () => {
    expect(durationSeconds({ amount: "10", unit: "m" })).toBe(600);
    expect(durationSeconds({ amount: " 5 ", unit: "s" })).toBe(5);
    expect(durationSeconds({ amount: "24", unit: "h" })).toBe(86_400);
    expect(durationSeconds({ amount: "25", unit: "h" })).toBeNull();
    expect(durationSeconds({ amount: "x", unit: "s" })).toBeNull();
  });

  it("báo lỗi dễ hiểu", () => {
    expect(durationError({ amount: "", unit: "s" })).toBe("Nhập số nguyên không âm, ví dụ 0, 30, 10");
    expect(durationError({ amount: "25", unit: "h" })).toBe("Tối đa 24 giờ");
    expect(durationError({ amount: "0", unit: "m" })).toBeNull();
  });
});

describe("credentialError", () => {
  it("không cho dấu cách, ký tự | và dấu : trong tên đăng nhập", () => {
    const username = { allowColon: false };
    expect(credentialError("user name", "Tên đăng nhập", username)).toBe(
      "Tên đăng nhập không được chứa dấu cách hoặc ký tự |",
    );
    expect(credentialError("a|b", "Tên đăng nhập", username)).toBe(
      "Tên đăng nhập không được chứa dấu cách hoặc ký tự |",
    );
    expect(credentialError("a:b", "Tên đăng nhập", username)).toBe("Tên đăng nhập không được chứa dấu ':'");
    expect(credentialError("user-session-{session}", "Tên đăng nhập", username)).toBeNull();
  });

  it("mật khẩu được có dấu :, giới hạn 255 ký tự tính theo code point", () => {
    const password = { allowColon: true };
    expect(credentialError("p:ss", "Mật khẩu", password)).toBeNull();
    expect(credentialError("x".repeat(256), "Mật khẩu", password)).toBe("Mật khẩu tối đa 255 ký tự");
    expect(credentialError("👍".repeat(255), "Mật khẩu", password)).toBeNull();
  });
});

describe("rotationUrlError", () => {
  it.each([
    ["", null],
    ["   ", null],
    ["https://4g.vn/api/change-ip?key=abc", null],
    [" http://10.0.0.1:8080/rotate ", null],
    ["ftp://4g.vn/change", "Link đổi IP phải bắt đầu bằng http:// hoặc https://"],
    ["4g.vn/change", "Link đổi IP không hợp lệ"],
    [`https://4g.vn/${"a".repeat(2000)}`, "Link đổi IP tối đa 2000 ký tự"],
  ])("%j → %s", (value, error) => {
    expect(rotationUrlError(value)).toBe(error);
  });
});
