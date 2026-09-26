from __future__ import annotations

import asyncio

from rich.console import Console
from rich.table import Table

from fb_poller.config import Settings
from fb_poller.orchestrator.scheduler import LocalJobQueue, scheduler_loop
from fb_poller.storage.db import init_db, session_scope
from fb_poller.storage.models import Tier
from fb_poller.storage.repo import PostRepo
from fb_poller.workers.browser_pool import BrowserPool
from fb_poller.workers.job_hot import poll_post
from fb_poller.workers.proxy_manager import ProxyManager

console = Console()


async def _worker_loop(
    slot: int,
    settings: Settings,
    queue: LocalJobQueue,
    pool: BrowserPool,
    proxy_manager: ProxyManager,
    stop: asyncio.Event,
    stats: dict[str, int],
    warm_cold_sem: asyncio.Semaphore,
) -> None:
    worker = pool.workers[slot]
    console.log(f"[worker-{slot}] ready proxy={worker.proxy.raw if worker.proxy else 'direct'}")
    while not stop.is_set():
        try:
            post = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        # Hot never waits on warm/cold semaphore — keeps realtime path free.
        need_sem = post.tier != Tier.hot.value
        if need_sem:
            await warm_cold_sem.acquire()
        try:
            outcome = await poll_post(
                settings=settings,
                pool=pool,
                worker=worker,
                post=post,
                proxy_manager=proxy_manager,
            )
            stats["jobs"] += 1
            stats["fetched"] += outcome.fetched
            stats["inserted"] += outcome.inserted_new
            if outcome.ok:
                stats["ok"] += 1
            else:
                stats["fail"] += 1
            console.log(
                f"[worker-{slot}] post={outcome.post_id} ok={outcome.ok} "
                f"new={outcome.inserted_new} fetched={outcome.fetched} "
                f"ms={outcome.latency_ms} err={outcome.error_code}"
            )
        except Exception as exc:
            stats["fail"] += 1
            console.log(f"[worker-{slot}] crash: {exc}")
        finally:
            if need_sem:
                warm_cold_sem.release()


async def run_poller(settings: Settings) -> None:
    await init_db(settings)
    queue = LocalJobQueue()
    stop = asyncio.Event()
    stats = {"jobs": 0, "ok": 0, "fail": 0, "fetched": 0, "inserted": 0}

    proxy_manager = ProxyManager(settings)
    assigned = await proxy_manager.load_for_worker(settings.workers)
    console.log(f"[runner] assigned_static_proxies={assigned} workers={settings.workers}")

    proxies = [proxy_manager.for_slot(i) for i in range(settings.workers)]
    pool = BrowserPool(settings)
    await pool.start(proxies, settings.workers)

    warm_cold_sem = asyncio.Semaphore(settings.warm_cold_max_inflight)
    tasks = [
        asyncio.create_task(scheduler_loop(settings, queue, stop), name="scheduler"),
    ]
    for i in range(settings.workers):
        tasks.append(
            asyncio.create_task(
                _worker_loop(i, settings, queue, pool, proxy_manager, stop, stats, warm_cold_sem),
                name=f"worker-{i}",
            )
        )

    async def _status() -> None:
        while not stop.is_set():
            await asyncio.sleep(15)
            async with session_scope() as session:
                counts = await PostRepo(session, settings).count_by_tier()
            table = Table(title="fb-poller status")
            table.add_column("metric")
            table.add_column("value")
            table.add_row("tiers", str(counts))
            table.add_row("queue", str(queue.qsize()))
            table.add_row("jobs", str(stats["jobs"]))
            table.add_row("ok/fail", f"{stats['ok']}/{stats['fail']}")
            table.add_row("inserted", str(stats["inserted"]))
            console.print(table)

    tasks.append(asyncio.create_task(_status(), name="status"))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        stop.set()
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await pool.close()
