import { describe, expect, it } from "vitest";

import { isSelected, pageSelectionOf, selectedCount, toggleOne, togglePage, type Selection } from "./selection";

const PAGE = [1, 2, 3];

describe("chọn từng proxy", () => {
  it("tích rồi bỏ tích", () => {
    const one = toggleOne(null, 2, true, PAGE);
    expect(one).toEqual({ mode: "ids", ids: new Set([2]) });
    expect(pageSelectionOf(one, PAGE)).toBe("some");
    expect(isSelected(one, 2)).toBe(true);
    expect(isSelected(one, 3)).toBe(false);
    expect(toggleOne(one, 2, false, PAGE)).toBeNull();
  });

  it("chọn cả trang rồi bỏ chọn cả trang vẫn giữ proxy đã chọn ở trang khác", () => {
    const otherPage = toggleOne(null, 9, true, [9, 10]);
    const withPage = togglePage(otherPage, PAGE, true);
    expect(pageSelectionOf(withPage, PAGE)).toBe("all");
    expect(selectedCount(withPage, 100)).toBe(4);
    expect(togglePage(withPage, PAGE, false)).toEqual({ mode: "ids", ids: new Set([9]) });
  });
});

describe("chọn tất cả proxy khớp bộ lọc", () => {
  const all: Selection = { mode: "all" };

  it("tính cả proxy ở trang chưa xem", () => {
    expect(isSelected(all, 12_345)).toBe(true);
    expect(selectedCount(all, 250)).toBe(250);
    expect(pageSelectionOf(all, PAGE)).toBe("all");
  });

  it("bỏ tích một proxy thì chỉ giữ các proxy khác trên trang đang xem", () => {
    expect(toggleOne(all, 2, false, PAGE)).toEqual({ mode: "ids", ids: new Set([1, 3]) });
    expect(toggleOne(all, 2, true, PAGE)).toBe(all);
  });

  it("bỏ chọn cả trang là bỏ chọn hết", () => {
    expect(togglePage(all, PAGE, false)).toBeNull();
    expect(togglePage(all, PAGE, true)).toBe(all);
  });
});

describe("không chọn gì", () => {
  it("trang trống hoặc chưa chọn", () => {
    expect(pageSelectionOf(null, [])).toBe("none");
    expect(pageSelectionOf(null, PAGE)).toBe("none");
    expect(selectedCount(null, 10)).toBe(0);
  });
});
