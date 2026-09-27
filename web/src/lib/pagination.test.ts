import { describe, expect, it } from "vitest";

import { pageCountOf, pageWindow } from "./pagination";

describe("pageWindow", () => {
  it.each([
    [1, 0, [1]],
    [1, 1, [1]],
    [3, 7, [1, 2, 3, 4, 5, 6, 7]],
    [1, 10, [1, 2, 3, 4, 5, null, 10]],
    [4, 10, [1, 2, 3, 4, 5, null, 10]],
    [5, 10, [1, null, 4, 5, 6, null, 10]],
    [7, 10, [1, null, 6, 7, 8, 9, 10]],
    [10, 10, [1, null, 6, 7, 8, 9, 10]],
    [99, 10, [1, null, 6, 7, 8, 9, 10]],
    [50, 100, [1, null, 49, 50, 51, null, 100]],
  ])("trang %s/%s → %j", (page, pageCount, expected) => {
    expect(pageWindow(page, pageCount)).toEqual(expected);
  });

  it("không bao giờ quá 7 ô", () => {
    for (let page = 1; page <= 40; page += 1) {
      expect(pageWindow(page, 40).length).toBeLessThanOrEqual(7);
    }
  });
});

describe("pageCountOf", () => {
  it("luôn có ít nhất 1 trang", () => {
    expect(pageCountOf(0, 50)).toBe(1);
    expect(pageCountOf(50, 50)).toBe(1);
    expect(pageCountOf(51, 50)).toBe(2);
  });
});
