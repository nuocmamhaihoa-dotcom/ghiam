import { describe, expect, it } from "vitest";

import { appendText, countProxyLines, pickLines, splitLines } from "./import-text";

describe("splitLines", () => {
  it("tách dòng giống str.splitlines() của Python", () => {
    expect(splitLines("")).toEqual([]);
    expect(splitLines("a")).toEqual(["a"]);
    expect(splitLines("\n")).toEqual([""]);
    expect(splitLines("a\n\n")).toEqual(["a", ""]);
    expect(splitLines("a\r\nb\rc\u2028d\u000be")).toEqual(["a", "b", "c", "d", "e"]);
  });
});

describe("countProxyLines", () => {
  it("bỏ dòng trống và dòng ghi chú", () => {
    const text = "1.1.1.1:80\n\n   \n# proxy Viettel\n// proxy Mobifone\n  2.2.2.2:80  \n";
    expect(countProxyLines(text)).toBe(2);
  });
});

describe("pickLines", () => {
  it("lấy lại đúng nguyên văn các dòng máy chủ báo lỗi", () => {
    expect(pickLines("a\n  b  \r\nc", [3, 2, 9])).toEqual(["c", "  b  "]);
  });
});

describe("appendText", () => {
  it("luôn ghép vào một dòng mới", () => {
    expect(appendText("", "x")).toBe("x");
    expect(appendText("  \n", "x")).toBe("x");
    expect(appendText("a", "x")).toBe("a\nx");
    expect(appendText("a\n", "x")).toBe("a\nx");
    expect(appendText("a\r\n", "x")).toBe("a\r\nx");
  });
});
