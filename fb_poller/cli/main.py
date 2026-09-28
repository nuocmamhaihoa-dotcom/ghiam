from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from fb_poller.config import get_settings
from fb_poller.journal import list_local, remember, remember_quietly
from fb_poller.orchestrator.runner import run_poller
from fb_poller.storage.db import init_db, session_scope
from fb_poller.storage.models import Comment, PollRun, Post, PostStatus, Tier
from fb_poller.storage.repo import PostRepo
from fb_poller.obs.metrics import Metrics
from fb_poller.workers.job_hot import poll_post
from fb_poller.workers.browser_pool import BrowserPool
from fb_poller.workers.proxy_manager import ProxyManager, import_proxy_files
from fb_poller.workers.types import PostJob

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


@app.command("init-db")
def init_db_cmd() -> None:
    """Create database tables."""
    settings = get_settings()
    remember_quietly(summary="Tạo schema database", kind="cli", source="cli")

    async def _run() -> None:
        await init_db(settings)
        console.print(f"[green]DB ready[/green] {settings.database_url}")

    asyncio.run(_run())


@app.command("import-urls")
def import_urls(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Text file, one Facebook post URL per line"),
) -> None:
    """Import post URLs into the database."""
    settings = get_settings()
    urls = [ln.strip() for ln in file.read_text(encoding="utf-8").splitlines()]
    remember_quietly(summary=f"Import URL từ {file} ({len(urls)} dòng)", kind="cli", source="cli")

    async def _run() -> None:
        await init_db(settings)
        async with session_scope() as session:
            inserted, skipped = await PostRepo(session, settings).upsert_urls(urls)
        console.print(f"inserted={inserted} skipped={skipped} total_lines={len(urls)}")

    asyncio.run(_run())


@app.command("import-proxies")
def import_proxies_cmd() -> None:
    """Import proxies from data/proxies_static.txt and data/proxies_4g.txt."""
    settings = get_settings()
    remember_quietly(summary="Import proxy từ file static/4g", kind="cli", source="cli")

    async def _run() -> None:
        await init_db(settings)
        result = await import_proxy_files(settings)
        console.print(result)

    asyncio.run(_run())


@app.command("rebalance-hot")
def rebalance_hot(
    hot_size: Optional[int] = typer.Option(None, help="Override HOT_SIZE"),
) -> None:
    """Force hot tier to HOT_SIZE posts."""
    settings = get_settings()
    if hot_size is not None:
        settings.hot_size = hot_size
    remember_quietly(summary=f"Rebalance hot size={settings.hot_size}", kind="cli", source="cli")

    async def _run() -> None:
        await init_db(settings)
        async with session_scope() as session:
            stats = await PostRepo(session, settings).rebalance_hot()
            counts = await PostRepo(session, settings).count_by_tier()
        console.print({"rebalance": stats, "tiers": counts})

    asyncio.run(_run())


@app.command("set-tier")
def set_tier(
    post_id: int,
    tier: str = typer.Argument(..., help="hot|warm|cold"),
) -> None:
    if tier not in {t.value for t in Tier}:
        raise typer.BadParameter("tier must be hot|warm|cold")
    settings = get_settings()
    remember_quietly(summary=f"Gán post {post_id} sang {tier}", kind="cli", source="cli")

    async def _run() -> None:
        await init_db(settings)
        async with session_scope() as session:
            await PostRepo(session, settings).set_tier(post_id, tier)
        console.print(f"post {post_id} -> {tier}")

    asyncio.run(_run())


@app.command("status")
def status_cmd() -> None:
    """Show counts and recent poll runs."""
    settings = get_settings()

    async def _run() -> None:
        await init_db(settings)
        async with session_scope() as session:
            tiers = await PostRepo(session, settings).count_by_tier()
            posts = await session.scalar(select(func.count()).select_from(Post))
            comments = await session.scalar(select(func.count()).select_from(Comment))
            hidden = await session.scalar(
                select(func.count()).select_from(Post).where(Post.status == PostStatus.guest_hidden.value)
            )
            runs = (
                await session.execute(select(PollRun).order_by(PollRun.id.desc()).limit(10))
            ).scalars().all()

        table = Table(title="fb-poller")
        table.add_column("key")
        table.add_column("value")
        table.add_row("posts", str(posts))
        table.add_row("comments", str(comments))
        table.add_row("guest_hidden", str(hidden))
        table.add_row("tiers", str(tiers))
        table.add_row("hot_interval_sec", str(settings.hot_interval_sec))
        table.add_row("workers", str(settings.workers))
        console.print(table)

        if runs:
            rt = Table(title="last poll_runs")
            rt.add_column("id")
            rt.add_column("post")
            rt.add_column("tier")
            rt.add_column("ms")
            rt.add_column("new")
            rt.add_column("err")
            for r in runs:
                rt.add_row(
                    str(r.id),
                    str(r.post_id),
                    r.tier,
                    str(r.latency_ms or "-"),
                    str(r.inserted_new),
                    r.error_code or "",
                )
            console.print(rt)

    asyncio.run(_run())


@app.command("probe")
def probe_cmd(
    limit: int = typer.Option(10, help="How many posts to probe"),
    workers: int = typer.Option(1, help="Browser workers for probe"),
) -> None:
    """Probe guest visibility for the first N active posts."""
    settings = get_settings()
    settings.workers = workers
    remember_quietly(summary=f"Probe {limit} bài với {workers} worker", kind="cli", source="cli")

    async def _run() -> None:
        await init_db(settings)
        async with session_scope() as session:
            posts = list(
                (
                    await session.scalars(
                        select(Post)
                        .where(Post.status == PostStatus.active.value)
                        .order_by(Post.id.asc())
                        .limit(limit)
                    )
                ).all()
            )
        if not posts:
            console.print("[yellow]No posts. import-urls first.[/yellow]")
            return

        proxy_manager = ProxyManager(settings)
        await proxy_manager.load_for_worker(workers)
        proxies = [proxy_manager.for_slot(i) for i in range(workers)]
        pool = BrowserPool(settings)
        await pool.start(proxies, workers)
        try:
            for idx, post in enumerate(posts):
                worker = pool.workers[idx % workers]
                job = PostJob(id=post.id, url=post.url, tier=post.tier)
                outcome = await poll_post(
                    settings=settings,
                    pool=pool,
                    worker=worker,
                    job=job,
                    proxy_manager=proxy_manager,
                )
                console.print(outcome)
        finally:
            await pool.close()

    asyncio.run(_run())


@app.command("doctor")
def doctor_cmd() -> None:
    """Check PC resources and local PA1 configuration."""
    from fb_poller.obs.doctor import run_doctor

    run_doctor()


@app.command("kpi")
def kpi_cmd(
    tail: int = typer.Option(500, help="Last N metric rows to analyze"),
) -> None:
    """Summarize PA1 KPI from metrics JSONL (gate before Hot @ 30s)."""
    settings = get_settings()
    path = settings.data_dir / "metrics" / f"{settings.worker_id}.jsonl"
    if not path.exists():
        console.print(f"[yellow]No metrics file yet:[/yellow] {path}")
        raise typer.Exit(1)

    lines = path.read_text(encoding="utf-8").splitlines()[-tail:]
    m = Metrics(window=tail)
    for line in lines:
        try:
            row = __import__("json").loads(line)
        except Exception:
            continue
        m.record(
            ok=bool(row.get("ok")),
            latency_ms=int(row.get("latency_ms") or 0),
            fetched=int(row.get("fetched") or 0),
            inserted=int(row.get("inserted") or 0),
            error_code=row.get("error_code"),
            post_id=int(row.get("post_id") or 0),
            tier=str(row.get("tier") or ""),
            worker_slot=int(row.get("worker_slot") or 0),
        )
    snap = m.snapshot()
    table = Table(title="PA1 KPI")
    table.add_column("key")
    table.add_column("value")
    for k, v in snap.items():
        table.add_row(k, str(v))
    console.print(table)
    if snap.get("ready_for_30s"):
        console.print("[green]Gate OK — can try HOT_INTERVAL_SEC=30[/green]")
    else:
        console.print("[yellow]Gate NOT ready — keep HOT_INTERVAL_SEC=45[/yellow]")


@app.command("run")
def run_cmd(
    workers: Optional[int] = typer.Option(None, help="Override WORKERS"),
    hot_interval: Optional[int] = typer.Option(None, help="Override HOT_INTERVAL_SEC"),
) -> None:
    """Start scheduler + browser workers (PA1)."""
    settings = get_settings()
    if workers is not None:
        settings.workers = workers
    if hot_interval is not None:
        settings.hot_interval_sec = hot_interval
    remember_quietly(
        summary=f"Bật poller {settings.workers} worker, hot {settings.hot_interval_sec}s",
        kind="cli",
        source="cli",
    )
    console.print(
        f"Starting PA1 poller workers={settings.workers} "
        f"hot_interval={settings.hot_interval_sec}s hot_size={settings.hot_size}"
    )
    try:
        asyncio.run(run_poller(settings))
    except KeyboardInterrupt:
        console.print("[yellow]stopped[/yellow]")


@app.command("sync-push")
def sync_push_cmd(
    control_url: str = typer.Option(..., "--control-url", envvar="CONTROL_URL", help="LAN server base URL"),
    machine_id: str = typer.Option("local", "--machine-id", envvar="MACHINE_ID"),
    token: str = typer.Option("", "--token", envvar="CONTROL_TOKEN"),
    limit: int = typer.Option(2000, help="Max comments to push per call"),
) -> None:
    """Push local comments to the high-bandwidth LAN PC server."""
    from fb_poller.cli.sync_push import push_comments

    settings = get_settings()
    remember_quietly(summary=f"Đẩy comment lên {control_url}", kind="cli", source="cli")

    async def _run() -> None:
        result = await push_comments(
            settings=settings,
            control_url=control_url,
            machine_id=machine_id,
            token=token,
            limit=limit,
        )
        console.print(result)

    asyncio.run(_run())


@app.command("note")
def note_cmd(
    text: str = typer.Argument(..., help="What you just did, in your own words"),
) -> None:
    """Remember one action so you can look it up later."""
    entry = remember(summary=text, kind="note", source="cli")
    if entry.get("pushed"):
        console.print(f"[green]Đã ghi[/green] {entry['at']} — {entry['summary']} (có trên hub)")
        return
    if os.environ.get("CONTROL_URL", "").strip():
        console.print(
            f"[green]Đã ghi trên máy này[/green] {entry['at']} — {entry['summary']}\n"
            "[yellow]Hub chưa nhận. Kiểm tra CONTROL_URL và CONTROL_TOKEN.[/yellow]"
        )
        return
    console.print(
        f"[green]Đã ghi trên máy này[/green] {entry['at']} — {entry['summary']}\n"
        "Xem lại: fb-poller actions"
    )


@app.command("actions")
def actions_cmd(
    q: str = typer.Option("", "--q", help="Filter by keyword"),
    limit: int = typer.Option(30, help="How many recent actions to show"),
) -> None:
    """Show actions you recorded on this machine."""
    rows = list_local(q=q, limit=limit)
    if not rows:
        console.print("[yellow]Chưa có thao tác nào được ghi.[/yellow]")
        console.print("Ghi tay: fb-poller note \"đã import URL và rebalance hot\"")
        return
    table = Table(title="Nhật ký thao tác")
    table.add_column("Thời gian")
    table.add_column("Loại")
    table.add_column("Thao tác")
    for row in rows:
        table.add_row(str(row.get("at") or ""), str(row.get("kind") or ""), str(row.get("summary") or ""))
    console.print(table)


if __name__ == "__main__":
    app()
