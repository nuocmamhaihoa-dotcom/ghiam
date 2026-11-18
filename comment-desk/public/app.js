const $ = (id) => document.getElementById(id);
let page = "home";

async function api(path, opt) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...opt,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "lỗi");
  return data;
}

function show(name) {
  page = name;
  for (const id of ["login", "home", "posts", "proxy", "pc", "nav"]) {
    $(id).classList.toggle("hide", id === "nav" ? name === "login" : id !== name);
  }
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function table(headers, rows) {
  const head = headers.map((h) => `<th>${esc(h)}</th>`).join("");
  const body = rows.map((r) => `<tr class="${r.ok ? "ok" : ""}">${r.cells.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${headers.length}">Chưa có</td></tr>`}</tbody></table>`;
}

function seatTable(title, rows) {
  const body = (rows || []).map((s) => ({
    ok: !!s.ok,
    cells: [s.progress || "", s.post || "", s.viec || "", s.ms ?? "", s.issue || "không"],
  }));
  return `<div class="panel"><h2>${title}</h2>${table(["Tiến trình", "Bài", "Việc", "ms", "Vấn đề"], body)}</div>`;
}

function renderHome(d) {
  const laneRows = (d.lanes || []).map((lane) => ({
    ok: lane.issue === "không",
    cells: [lane.name, lane.doing, lane.today, lane.issue],
  }));
  const pcRows = (d.pcs || []).map((p) => ({
    ok: !p.homeBurned,
    cells: [
      p.host,
      p.homeBurned ? "đứng GET" : "đang nối",
      `${p.cpu || 0}% / ${p.cores || "?"} nhân`,
      p.ram || "",
      p.homeBurned ? "im" : "Hướng A",
      `${p.note || "không"}${p.renew ? " · " + p.renew.status : ""}`,
    ],
  }));
  const commentRows = (d.comments || []).map((c, i) => ({
    cells: [i + 1, c.fbTime || "—", c.scannedAt || "", c.faceId || "—", c.name || "—", c.postId || "—", c.postTitle || "—", c.text || ""],
  }));
  const f = d.facts || {};
  $("home").innerHTML = `
    <div class="kpis">
      <div class="card kpi"><b>${esc(d.posts)}</b><span>Tổng số bài</span><div class="muted">${esc(d.scanning)} bài Lean 500</div></div>
      <div class="card kpi"><b>${esc(d.scanning)}</b><span>Đang quét liên tục</span><div class="muted">A nhà ${esc(d.homeDoors)} GET · leftover ${esc(d.doors)} GET</div></div>
      <div class="card kpi"><b>${esc(d.today)}</b><span>Tương tác hôm nay</span><div class="muted">comment khách lưu hôm nay</div></div>
      <div class="card kpi"><b>${esc(d.liveProxies)}/${esc(d.proxies)}</b><span>Proxy 4G sẵn sàng</span><div class="muted">hosting lưu ${esc(d.staticProxies || 0)} · không đưa vào GET</div></div>
    </div>
    <h2>Hướng đang quét · việc và tiến trình</h2>
    <div class="panel">
      <div class="banner">${esc(d.banner)}</div>
      ${d.sample ? `<p class="muted">Dữ liệu mẫu. Đặt mật khẩu quản trị để dùng dữ liệu thật.</p>` : ""}
      ${table(["Hướng", "Việc đang làm", "Hôm nay", "Vấn đề"], laneRows)}
      <p class="muted">Leftover hôm nay: mới ${esc(f.mới || 0)} · cũ ${esc(f.cũ || 0)} · im ${esc(f.im || 0)} · timeout ${esc(f.timeout || 0)} · 429 ${esc(f["429"] || 0)} · counts_shell ${esc(f.counts_shell || 0)} · fetch failed ${esc(f.fetch_failed || 0)}</p>
    </div>
    <div class="split">
      ${seatTable("A · ghế PC", d.seats.A)}
      ${seatTable("D · kết quả đọc", d.seats.D)}
      ${seatTable("G · radar nghe bài", d.seats.G)}
      ${seatTable("Leftover 4G · www guest", d.seats.leftover)}
    </div>
    <h2>PC nhà · việc / kết quả / vấn đề</h2>
    <div class="panel">
      ${table(["PC", "Kết nối", "CPU", "RAM", "Hướng", "Vấn đề"], pcRows)}
    </div>
    <h2>Thống kê tương tác · khách viết hôm nay</h2>
    <div class="panel">
      <p class="muted">${esc(d.today)} bình luận khách lưu hôm nay. Mới là chữ vừa đọc. Cũ là chữ đã có.</p>
      ${table(["STT", "Giờ Facebook (GMT+7)", "Lúc quét (GMT+7)", "Face ID", "Tên Facebook", "Post", "Tên bài", "Comment"], commentRows)}
    </div>
    <h2>Nhật ký mất chữ</h2>
    <div class="panel">
      <p class="muted">Có tín hiệu tăng số, nhưng GET ra im, 429, vỏ đếm, timeout hoặc không có legacy_fbid.</p>
      ${table(["Lúc", "Bài", "Kết quả"], (d.misses || []).map((row) => ({ cells: [row.at, row.postId, row.kind] })))}
    </div>`;
}

async function refresh() {
  if (page !== "home" || $("home").classList.contains("hide")) return;
  try {
    renderHome(await api("/api/home"));
  } catch {
    show("login");
  }
}

document.querySelectorAll("#nav button[data-page]").forEach((btn) => {
  btn.onclick = async () => {
    show(btn.dataset.page);
    if (btn.dataset.page === "posts") {
      const data = await api("/api/posts");
      $("postText").value = data.posts.map((p) => p.url).join("\n");
      const hotSet = new Set(data.hotIds || []);
      $("hotText").value = data.posts.filter((p) => hotSet.has(p.id)).map((p) => p.url).join("\n");
    }
    if (btn.dataset.page === "proxy") {
      const data = await api("/api/proxies");
      $("proxyTable").innerHTML = table(
        ["Host", "Cổng", "Xoay", "Trạng thái"],
        data.proxies.map((p) => ({ ok: p.status === "Live", cells: [p.host, p.port, p.rotate, p.status] })),
      );
    }
    if (btn.dataset.page === "pc") {
      const data = await api("/api/pc-token");
      $("pcToken").textContent = data.token;
      $("pcHelp").textContent = `Tải CaiDat-PC.exe và chạy trên từng PC.\nToken: ${data.token}\nSlot 1 bài 1-32, slot 2 bài 33-64, slot 3 bài 65-96, slot 4 bài 97-128.\nBản mới được tải và khởi động lại ngay.`;
    }
    refresh();
  };
});

if (new URLSearchParams(location.search).get("loi") === "1") {
  $("loginErr").textContent = "Sai tài khoản hoặc mật khẩu";
}

$("out").onclick = async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  show("login");
};

$("saveHot").onclick = async () => {
  const data = await api("/api/hot", { method: "POST", body: JSON.stringify({ text: $("hotText").value }) });
  $("hotMsg").textContent = `Đã gắn ${data.count} bài nóng`;
};

$("savePosts").onclick = async () => {
  const data = await api("/api/posts", { method: "POST", body: JSON.stringify({ text: $("postText").value }) });
  $("postMsg").textContent = `Đã lưu ${data.count} bài`;
};

$("saveProxy").onclick = async () => {
  const data = await api("/api/proxies", { method: "POST", body: JSON.stringify({ text: $("proxyText").value }) });
  if ($("staticText").value.trim()) {
    await api("/api/proxies", { method: "POST", body: JSON.stringify({ kind: "static", text: $("staticText").value }) });
  }
  $("proxyMsg").textContent = `Đã lưu ${data.count} line 4G`;
};

api("/api/me").then((me) => {
  if (!me.ok) {
    show("login");
    return;
  }
  $("who").textContent = me.user;
  show("home");
  refresh();
}).catch(() => show("login"));

setInterval(refresh, 3000);
