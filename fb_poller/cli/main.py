from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from fb_poller.config import get_settings
from fb_poller.orchestrator.runner import run_poller
from fb_poller.storage.db import init_db, session_scope
from fb_poller.storage.models import Comment, PollRun, Post, PostStatus, Tier
from fb_poller.storage.repo import PostRepo
from fb_poller.workers.job_hot import poll_post
from fb_poller.workers.browser_pool import BrowserPool
from fb_poller.workers.proxy_manager import ProxyManager, import_proxy_files

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


@app.command("init-db")
def init_db_cmd() -> None:
    """Create database tables."""
    settings = get_settings()

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
                # Detach instance fields used after session closed
                outcome = await poll_post(
                    settings=settings,
                    pool=pool,
                    worker=worker,
                    post=post,
                    proxy_manager=proxy_manager,
                )
                console.print(outcome)
        finally:
            await pool.close()

    asyncio.run(_run())


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
    console.print(
        f"Starting PA1 poller workers={settings.workers} "
        f"hot_interval={settings.hot_interval_sec}s hot_size={settings.hot_size}"
    )
    try:
        asyncio.run(run_poller(settings))
    except KeyboardInterrupt:
        console.print("[yellow]stopped[/yellow]")


if __name__ == "__main__":
    app()
