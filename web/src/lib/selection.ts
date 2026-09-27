/** "ids": các proxy được tích từng cái (có thể ở nhiều trang). "all": mọi proxy khớp bộ lọc, kể cả trang chưa xem. */
export type Selection = { mode: "ids"; ids: ReadonlySet<number> } | { mode: "all" };

export type PageSelection = "all" | "some" | "none";

export function isSelected(selection: Selection | null, id: number): boolean {
  if (selection === null) {
    return false;
  }
  return selection.mode === "all" || selection.ids.has(id);
}

export function pageSelectionOf(selection: Selection | null, pageIds: readonly number[]): PageSelection {
  const count = pageIds.filter((id) => isSelected(selection, id)).length;
  if (count === 0) {
    return "none";
  }
  return count === pageIds.length ? "all" : "some";
}

export function selectedCount(selection: Selection | null, total: number): number {
  if (selection === null) {
    return 0;
  }
  return selection.mode === "all" ? total : selection.ids.size;
}

function fromIds(ids: ReadonlySet<number>): Selection | null {
  return ids.size > 0 ? { mode: "ids", ids } : null;
}

/** Bỏ tích một proxy khi đang chọn "tất cả" thì chỉ giữ lại các proxy khác trên trang đang xem. */
export function toggleOne(
  selection: Selection | null,
  id: number,
  selected: boolean,
  pageIds: readonly number[],
): Selection | null {
  if (selection?.mode === "all") {
    return selected ? selection : fromIds(new Set(pageIds.filter((item) => item !== id)));
  }
  const ids = new Set(selection?.ids);
  if (selected) {
    ids.add(id);
  } else {
    ids.delete(id);
  }
  return fromIds(ids);
}

export function togglePage(
  selection: Selection | null,
  pageIds: readonly number[],
  selected: boolean,
): Selection | null {
  if (selection?.mode === "all") {
    return selected ? selection : null;
  }
  const ids = new Set(selection?.ids);
  for (const id of pageIds) {
    if (selected) {
      ids.add(id);
    } else {
      ids.delete(id);
    }
  }
  return fromIds(ids);
}
