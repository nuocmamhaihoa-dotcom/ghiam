/* Đọc chữ đang hiện khi dừng lướt. Danh bạ cho hai dòng. Hồ sơ cho tên và @. */
(function () {
  if (window.__peopleWatch) return;
  window.__peopleWatch = true;
  const skip = /^(follow|tin nhắn|đã follow|follower|thích|từ các liên hệ của bạn|danh bạ)$/i;
  const handle = /^@[A-Za-z0-9._]{2,30}$/;
  const sent = new Set();
  const known = new Map();
  let ready = false;

  function nameKey(name) {
    return String(name || "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
  }

  function remember(rows) {
    (rows || []).forEach((row) => {
      if (!row || !row.name) return;
      known.set(nameKey(row.name), row);
    });
  }

  function fresh(item) {
    const row = known.get(nameKey(item.name));
    if (!row) return true;
    if (item.kind === "contact") return !row.contactName;
    if (item.kind === "profile") return !row.username;
    return false;
  }

  function visible(el) {
    const box = el.getBoundingClientRect();
    return box.bottom > 0 && box.top < window.innerHeight && box.width > 0 && box.height > 0;
  }

  function linesOf(row, button) {
    const seen = [];
    const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (button && button.contains(node)) continue;
      const text = node.textContent.replace(/\s+/g, " ").trim();
      if (!text || text.length > 80 || skip.test(text) || /^\d+$/.test(text)) continue;
      if (seen[seen.length - 1] !== text) seen.push(text);
    }
    return seen;
  }

  function rowFor(button) {
    let el = button.parentElement;
    while (el && el !== document.body) {
      const follows = Array.from(el.querySelectorAll("button, a")).filter((node) => {
        return node.innerText.replace(/\s+/g, " ").trim().toLowerCase() === "follow";
      });
      if (follows.length === 1) return el;
      el = el.parentElement;
    }
    return button.parentElement;
  }

  function classify(lines) {
    const username = lines.find((line) => handle.test(line)) || "";
    const names = lines.filter((line) => line !== username);
    if (username && names.length) {
      return { kind: "profile", name: names[0], username: username, contactName: "" };
    }
    if (!username && names.length >= 2) {
      return { kind: "contact", name: names[1], contactName: names[0], username: "" };
    }
    return null;
  }

  function scan() {
    const items = [];
    document.querySelectorAll("button, a").forEach((button) => {
      if (button.innerText.replace(/\s+/g, " ").trim().toLowerCase() !== "follow") return;
      const row = rowFor(button);
      if (!row || !visible(row)) return;
      const item = classify(linesOf(row, button));
      if (!item || !fresh(item)) return;
      const key = item.kind + "|" + item.name + "|" + item.contactName + "|" + item.username;
      if (sent.has(key)) return;
      sent.add(key);
      items.push(item);
    });
    return items;
  }

  const queue = [];

  async function publish() {
    const items = queue.splice(0, 40);
    if (!items.length) return;
    const token = localStorage.getItem("fb_poller_control_token") || "";
    const headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = "Bearer " + token;
    const response = await fetch("/v1/people/sightings", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ items: items }),
    });
    if (response.status === 401) {
      document.dispatchEvent(new CustomEvent("people-auth"));
      tell({ type: "people-auth" });
      return;
    }
    if (!response.ok) return;
    const data = await response.json();
    remember(data.known || []);
    const saved = data.items || [];
    document.dispatchEvent(new CustomEvent("people-saved", { detail: saved }));
    tell({ type: "people-saved", items: saved });
    if (queue.length) {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => { publish(); }, 50);
    }
  }

  function tell(message) {
    try {
      if (window.parent && window.parent !== window) window.parent.postMessage(message, location.origin);
    } catch (err) { /* khung khác nguồn thì bỏ qua */ }
    try {
      const channel = new BroadcastChannel("fb-people");
      channel.postMessage(message);
      channel.close();
    } catch (err) { /* trình duyệt không có kênh */ }
  }

  let timer = 0;
  function schedule() {
    if (!ready) return;
    queue.push(...scan());
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { publish(); }, 250);
  }

  window.addEventListener("scroll", schedule, true);
  window.addEventListener("load", schedule);
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) schedule();
    }, { threshold: 0.15 });
    document.querySelectorAll("button, a").forEach((button) => {
      if (button.innerText.replace(/\s+/g, " ").trim().toLowerCase() !== "follow") return;
      observer.observe(rowFor(button) || button);
    });
  }
  async function prime() {
    const token = localStorage.getItem("fb_poller_control_token") || "";
    const headers = {};
    if (token) headers.Authorization = "Bearer " + token;
    try {
      const response = await fetch("/v1/people", { headers: headers, cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        remember(data.known || data.items || []);
      }
    } catch (err) { /* mất mạng thì vẫn đọc màn hình, hub lọc phần đã lưu */ }
    ready = true;
    schedule();
  }
  prime();
})();
