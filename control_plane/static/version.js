/* Hỏi hub xem bản app đã đổi chưa. Bản mới thì tải lại một lần. Mất mạng thì giữ bản đang mở. */
(function () {
  const meta = document.querySelector('meta[name="iphone-build"]');
  if (!meta) return;
  const build = Number(meta.content);
  if (!build) return;
  const note = document.getElementById("updateNote");

  function showUpdated() {
    if (!note) return;
    note.hidden = false;
    note.textContent = "Đã cập nhật";
  }

  async function check() {
    let remote = 0;
    try {
      const response = await fetch("/health", { cache: "no-store" });
      if (!response.ok) return;
      const body = await response.json();
      remote = Number(body.iphoneBuild || 0);
    } catch (err) {
      return;
    }
    if (!remote || remote === build) return;
    const key = "fb_poller_iphone_reloaded";
    if (sessionStorage.getItem(key) === String(remote)) {
      showUpdated();
      return;
    }
    sessionStorage.setItem(key, String(remote));
    const url = new URL(location.href);
    url.searchParams.set("build", String(remote));
    location.replace(url.toString());
  }

  if (new URLSearchParams(location.search).get("build") === String(build)) showUpdated();
  check();
  window.setInterval(check, 180000);
})();
