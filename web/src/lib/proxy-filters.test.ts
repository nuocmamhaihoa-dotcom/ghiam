import { describe, expect, it } from "vitest";

import {
  DEFAULT_PAGE_SIZE,
  DEFAULT_SORT,
  countFilters,
  filtersOf,
  paramsFromSearch,
  searchFromParams,
} from "./proxy-filters";
import type { ProxyListParams } from "./types";

describe("paramsFromSearch", () => {
  it("dùng giá trị mặc định khi URL không có tham số", () => {
    expect(paramsFromSearch(new URLSearchParams())).toEqual({
      sort: DEFAULT_SORT,
      page: 1,
      page_size: DEFAULT_PAGE_SIZE,
    });
  });

  it("đọc các bộ lọc hợp lệ và bỏ qua giá trị lạ", () => {
    const search = new URLSearchParams(
      "q=+vn+&kind=rotating&health=zombie&pool=vn-hcm&enabled=false&leased=yes&rotation_state=pending&quarantined=true&sort=latency&page=3&page_size=100",
    );
    expect(paramsFromSearch(search)).toEqual({
      q: "vn",
      kind: "rotating",
      pool: "vn-hcm",
      enabled: false,
      rotation_state: "pending",
      quarantined: true,
      sort: "latency",
      page: 3,
      page_size: 100,
    });
  });

  it("bỏ số trang, cỡ trang và cách sắp xếp không hợp lệ", () => {
    expect(paramsFromSearch(new URLSearchParams("page=0&page_size=37"))).toMatchObject({
      page: 1,
      page_size: DEFAULT_PAGE_SIZE,
    });
    expect(paramsFromSearch(new URLSearchParams("page=-2&sort=hack"))).toMatchObject({ page: 1, sort: DEFAULT_SORT });
    expect(paramsFromSearch(new URLSearchParams("page=abc&q=%20%20"))).toEqual({
      sort: DEFAULT_SORT,
      page: 1,
      page_size: DEFAULT_PAGE_SIZE,
    });
  });
});

describe("searchFromParams", () => {
  it("chỉ ghi giá trị khác mặc định và đọc lại được y như cũ", () => {
    const params: ProxyListParams = {
      q: "1.2.3.4",
      health: "dead",
      leased: true,
      sort: "-last_checked",
      page: 2,
      page_size: 25,
    };
    const search = searchFromParams(params);
    expect(search.toString()).toBe("q=1.2.3.4&health=dead&leased=true&sort=-last_checked&page=2&page_size=25");
    expect(paramsFromSearch(search)).toEqual(params);
  });

  it("trang mặc định cho URL rỗng", () => {
    expect(searchFromParams({ sort: DEFAULT_SORT, page: 1, page_size: DEFAULT_PAGE_SIZE }).toString()).toBe("");
  });
});

describe("filtersOf", () => {
  it("bỏ phân trang, sắp xếp và điều kiện rỗng, giữ giá trị false", () => {
    const params: ProxyListParams = {
      q: "",
      kind: "static",
      enabled: false,
      pool: undefined,
      sort: "host",
      page: 4,
      page_size: 200,
    };
    expect(filtersOf(params)).toEqual({ kind: "static", enabled: false });
    expect(countFilters(params)).toBe(2);
    expect(countFilters({})).toBe(0);
  });
});
