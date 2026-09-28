/* Đọc chữ đang hiện khi dừng lướt. Danh bạ cho hai dòng. Hồ sơ cho tên và @. */
(function () {
  const skip = /^(follow|tin nhắn|đã follow|follower|thích|từ các liên hệ của bạn|danh bạ)$/i;
  const handle = /^@[A-Za-z0-9._]{2,30}$/;
  const sent = new Set();

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
      if (!visible(button)) return;
      const row = rowFor(button);
      if (!row || !visible(row)) return;
      const item = classify(linesOf(row, button));
      if (!item) return;
      const key = item.kind + "|" + item.name + "|" + item.contactName + "|" + item.username;
      if (sent.has(key)) return;
      sent.add(key);
      items.push(item);
    });
    return items;
  }

  async function publish() {
    const items = scan();
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
      return;
    }
    if (!response.ok) return;
    const data = await response.json();
    document.dispatchEvent(new CustomEvent("people-saved", { detail: data.items || [] }));
  }

  let timer = 0;
  function schedule() {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { publish(); }, 400);
  }

  window.addEventListener("scroll", schedule, true);
  window.addEventListener("load", schedule);
  schedule();
})();
