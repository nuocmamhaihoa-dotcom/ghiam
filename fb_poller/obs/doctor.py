"""Kiểm tra tài nguyên PC và cấu hình PA1 trước khi quét."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from rich.console import Console
from rich.table import Table

from fb_poller.config import ROOT, get_settings

console = Console()


def cpu_count() -> int:
    return os.cpu_count() or 1


def ram_gb() -> float:
    try:
        mem = Path("/proc/meminfo").read_text(encoding="utf-8")
        for line in mem.splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return round(kb / 1024 / 1024, 1)
    except Exception:
        pass
    return -1.0


def count_nonempty_lines(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            n += 1
    return n


def run_doctor() -> None:
    settings = get_settings()
    cores = cpu_count()
    ram = ram_gb()
    suggested = max(1, min(14, cores * 7 // 10))

    table = Table(title="PC Doctor — fb-poller PA1")
    table.add_column("Hạng mục")
    table.add_column("Giá trị")
    table.add_column("Gợi ý")

    table.add_row("CPU cores", str(cores), f"WORKERS≈{suggested}")
    table.add_row("RAM GiB", str(ram), "≥32GB khuyến nghị cho 10+ workers")
    table.add_row("WORKERS (.env)", str(settings.workers), "kq2: 10 (1 browser / 1 proxy 4G)")
    table.add_row("HOT_SIZE", str(settings.hot_size), "100")
    table.add_row("HOT_INTERVAL_SEC", str(settings.hot_interval_sec), "45 trước, 30 sau KPI")
    table.add_row("HEADLESS", str(settings.headless), "true trên server/PC headless")
    chromium_ok = (ROOT / ".venv" / "bin" / "playwright").exists() or shutil.which("playwright") is not None
    table.add_row("Playwright", "ok" if chromium_ok else "missing", "playwright install chromium")

    posts = count_nonempty_lines(ROOT / "data" / "posts.txt")
    static = count_nonempty_lines(Path(settings.proxies_static_file))
    g4 = count_nonempty_lines(Path(settings.proxies_4g_file))
    table.add_row("posts.txt", str(posts), "≥1 URL permalink")
    table.add_row("proxies_static", str(static), "dự phòng (gói tĩnh hết hạn thì bỏ)")
    table.add_row("proxies_4g", str(g4), "kq2: 10 proxy, ưu tiên quét")
    rotate_n = count_nonempty_lines(Path(settings.proxies_4g_rotate_file))
    table.add_row("proxies_4g_rotate", str(rotate_n), "xoay IP mỗi 5 phút, lệch nhau")
    table.add_row("DB", settings.database_url, "SQLite 1 máy / Postgres khi 2 PC")
    console.print(table)

    warns: list[str] = []
    if settings.workers > suggested + 2:
        warns.append(f"WORKERS={settings.workers} cao hơn gợi ý {suggested} — dễ timeout/CPU full.")
    if posts == 0:
        warns.append("Chưa có URL trong data/posts.txt")
    if g4 == 0 and static < settings.workers:
        warns.append(
            f"Không có proxy 4G kq2 và proxy tĩnh ({static}) < WORKERS ({settings.workers})."
        )
    if g4 and rotate_n < g4:
        warns.append(f"Thiếu link xoay 4G ({rotate_n}/{g4}).")
    if g4 and settings.workers > g4:
        warns.append(
            f"WORKERS={settings.workers} > {g4} proxy 4G — worker thừa dùng chung IP 4G."
        )
    if settings.workers >= 10 and ram > 0 and ram < 24:
        warns.append("RAM thấp so với số worker — giảm WORKERS.")

    if warns:
        console.print("\n[yellow]Cảnh báo:[/yellow]")
        for w in warns:
            console.print(f"  - {w}")
    else:
        console.print("\n[green]OK — có thể start:[/green] ./scripts/fb-poller-ctl.sh start")

    console.print("\nLệnh nhanh:")
    console.print("  ./scripts/fb-poller-ctl.sh import")
    console.print("  ./scripts/fb-poller-ctl.sh probe 10 2")
    console.print("  ./scripts/fb-poller-ctl.sh start")
    console.print("  ./scripts/fb-poller-ctl.sh status")
