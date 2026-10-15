"use strict";

const $ = (id) => document.getElementById(id);
let controller = null;

function setStatus(message) {
  $("message").textContent = message;
}

function safeHttpUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" && url.protocol !== "http:") return "";
    return url.href;
  } catch {
    return "";
  }
}

function text(parent, tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value || "";
  parent.append(node);
  return node;
}

function externalLink(parent, label, href) {
  const safe = safeHttpUrl(href);
  if (!safe) return;
  const link = document.createElement("a");
  link.href = safe;
  link.text = label;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  parent.append(link);
}

function firstText(value) {
  return Array.isArray(value) && value.length ? String(value[0]) : "";
}

function libraryUrl(adId) {
  return `https://www.facebook.com/ads/library/?id=${encodeURIComponent(adId)}`;
}

function renderCard(ad) {
  const card = document.createElement("article");
  card.className = "card";
  text(card, "h3", "", ad.page_name || "Quảng cáo");
  text(card, "p", "meta", `Mã thư viện: ${ad.id || "—"}`);
  text(card, "p", "text", firstText(ad.ad_creative_bodies) || "Không có nội dung chữ.");
  const title = firstText(ad.ad_creative_link_titles);
  if (title) text(card, "p", "text", title);
  const platforms = Array.isArray(ad.publisher_platforms) ? ad.publisher_platforms.join(", ") : "";
  text(
    card,
    "p",
    "dates",
    `Bắt đầu: ${ad.ad_delivery_start_time || "—"}${ad.ad_delivery_stop_time ? ` · Dừng: ${ad.ad_delivery_stop_time}` : ""}${platforms ? ` · ${platforms}` : ""}`,
  );
  const actions = document.createElement("div");
  actions.className = "actions";
  externalLink(actions, "Mở trong Thư viện", libraryUrl(String(ad.id || "")));
  externalLink(actions, "Xem ảnh chụp", ad.ad_snapshot_url || "");
  card.append(actions);
  return card;
}

function renderResults(page, ads, keyword) {
  const cards = document.createDocumentFragment();
  for (const ad of ads) cards.append(renderCard(ad));
  $("results").replaceChildren(cards);
  $("count").textContent = String(ads.length);
  if (page?.name && keyword) {
    $("subject-label").textContent = "Fanpage + từ khóa";
    $("page-name").textContent = `${page.name} · ${keyword}`;
  } else if (page?.name) {
    $("subject-label").textContent = "Fanpage";
    $("page-name").textContent = page.name;
  } else if (keyword) {
    $("subject-label").textContent = "Từ khóa";
    $("page-name").textContent = keyword;
  } else {
    $("subject-label").textContent = "Phạm vi";
    $("page-name").textContent = "—";
  }

  const body = $("table-body");
  body.replaceChildren();
  ads.forEach((ad, index) => {
    const row = document.createElement("tr");
    text(row, "td", "", String(index + 1));
    text(row, "td", "", ad.page_name || page?.name || "—");
    const idCell = text(row, "td", "", "");
    externalLink(idCell, String(ad.id || ""), libraryUrl(String(ad.id || "")));
    if (!idCell.childNodes.length) idCell.textContent = String(ad.id || "");
    text(row, "td", "", ad.ad_delivery_start_time || "—");
    text(row, "td", "", Array.isArray(ad.publisher_platforms) ? ad.publisher_platforms.join(", ") : "—");
    text(row, "td", "", firstText(ad.ad_creative_link_titles) || firstText(ad.ad_creative_bodies) || "—");
    body.append(row);
  });
  $("table-empty").hidden = ads.length > 0;
}

function setTab(name) {
  const cards = name === "cards";
  $("results").hidden = !cards;
  $("table-panel").hidden = cards;
  $("cards-tab").classList.toggle("active", cards);
  $("table-tab").classList.toggle("active", !cards);
  $("cards-tab").setAttribute("aria-selected", String(cards));
  $("table-tab").setAttribute("aria-selected", String(!cards));
}

$("cards-tab").addEventListener("click", () => setTab("cards"));
$("table-tab").addEventListener("click", () => setTab("table"));

async function showSetup() {
  try {
    const response = await fetch("/api/ready");
    const data = await response.json();
    if (!data.configured) {
      const note = $("setup");
      note.hidden = false;
      note.textContent = "Máy chủ chưa được Meta cấp quyền đọc thư viện quảng cáo, nên bấm Tìm sẽ chưa ra danh sách.";
    }
  } catch {
    setStatus("Không đọc được trạng thái máy chủ.");
  }
}

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (controller) controller.abort();
  controller = new AbortController();
  const button = $("go");
  button.disabled = true;
  button.textContent = "Đang tìm...";
  const keyword = $("keyword").value.trim();
  const url = $("fanpage").value.trim();
  if (!keyword && !url) {
    setStatus("Nhập từ khóa hoặc link Fanpage.");
    button.disabled = false;
    button.textContent = "Tìm quảng cáo";
    controller = null;
    return;
  }
  setStatus("Đang hỏi Meta Ad Library API.");
  renderResults(null, [], "");
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        keyword,
        match: $("match").value,
        country: $("country").value,
        status: $("status").value,
        limit: 50,
      }),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      const error = data.error || "Không tìm được quảng cáo.";
      setStatus(
        error.includes("META_ACCESS_TOKEN")
          ? "Máy chủ chưa được Meta cấp quyền đọc thư viện quảng cáo, nên chưa có danh sách."
          : error,
      );
      return;
    }
    renderResults(data.page, data.ads || [], data.keyword || "");
    const scope = [data.page?.name, data.keyword].filter(Boolean).join(" · ") || "bộ lọc này";
    setStatus(
      data.count
        ? `Đã tải ${data.count} quảng cáo: ${scope}.`
        : "Không thấy quảng cáo khớp bộ lọc. Token có thể chỉ xem được quảng cáo chính trị, hoặc không có quảng cáo ở quốc gia này.",
    );
  } catch (error) {
    if (error.name !== "AbortError") setStatus("Không kết nối được máy chủ.");
  } finally {
    button.disabled = false;
    button.textContent = "Tìm quảng cáo";
    controller = null;
  }
});

showSetup();
