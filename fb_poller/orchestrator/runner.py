from __future__ import annotations

import asyncio
import signal

from rich.console import Console
from rich.table import Table

from fb_poller.config import Settings
from fb_poller.obs.metrics import Metrics
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
    metrics: Metrics,
    warm_cold_sem: asyncio.Semaphore,
) -> None:
    worker = pool.workers[slot]
    console.log(f"[worker-{slot}] ready proxy={worker.proxy.raw if worker.proxy else 'direct'}")
    while not stop.is_set():
        try:
            job = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        need_sem = job.tier != Tier.hot.value
        if need_sem:
            await warm_cold_sem.acquire()
        try:
            outcome = await poll_post(
                settings=settings,
                pool=pool,
                worker=worker,
                job=job,
                proxy_manager=proxy_manager,
            )
            metrics.record(
                ok=outcome.ok,
                latency_ms=outcome.latency_ms,
                fetched=outcome.fetched,
                inserted=outcome.inserted_new,
                error_code=outcome.error_code,
                post_id=outcome.post_id,
                tier=job.tier,
                worker_slot=slot,
            )
            console.log(
                f"[worker-{slot}] post={outcome.post_id} tier={job.tier} ok={outcome.ok} "
                f"new={outcome.inserted_new} fetched={outcome.fetched} "
                f"ms={outcome.latency_ms} retry={outcome.retries} err={outcome.error_code}"
            )
        except Exception as exc:
            metrics.record(
                ok=False,
                latency_ms=0,
                fetched=0,
                inserted=0,
                error_code="crash",
                post_id=job.id,
                tier=job.tier,
                worker_slot=slot,
            )
            console.log(f"[worker-{slot}] crash: {exc}")
        finally:
            if need_sem:
                warm_cold_sem.release()


async def run_poller(settings: Settings) -> None:
    await init_db(settings)
    queue = LocalJobQueue()
    stop = asyncio.Event()
    metrics_path = settings.data_dir / "metrics" / f"{settings.worker_id}.jsonl"
    metrics = Metrics(path=metrics_path)

    proxy_manager = ProxyManager(settings)
    assigned = await proxy_manager.load_for_worker(settings.workers)
    console.log(
        f"[runner] PA1 workers={settings.workers} proxies={assigned} "
        f"hot_interval={settings.hot_interval_sec}s hot_size={settings.hot_size}"
    )

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
                _worker_loop(i, settings, queue, pool, proxy_manager, stop, metrics, warm_cold_sem),
                name=f"worker-{i}",
            )
        )

    async def _status() -> None:
        while not stop.is_set():
            await asyncio.sleep(15)
            async with session_scope() as session:
                counts = await PostRepo(session, settings).count_by_tier()
            snap = metrics.snapshot()
            table = Table(title="fb-poller PA1 status")
            table.add_column("metric")
            table.add_column("value")
            table.add_row("tiers", str(counts))
            table.add_row("queue", str(queue.qsize()))
            table.add_row("jobs", str(snap["jobs"]))
            table.add_row("success_rate", str(snap["success_rate"]))
            table.add_row("p50/p95_ms", f"{snap['p50_ms']}/{snap['p95_ms']}")
            table.add_row("inserted", str(snap["inserted"]))
            table.add_row("errors", str(snap["errors"]))
            table.add_row("ready_for_30s", str(snap["ready_for_30s"]))
            console.print(table)

    tasks.append(asyncio.create_task(_status(), name="status"))

    loop = asyncio.get_running_loop()

    def _ask_stop() -> None:
        console.log("[runner] shutdown signal")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _ask_stop)
        except NotImplementedError:
            pass

    try:
        while not stop.is_set():
            await asyncio.sleep(0.5)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pool.close()
        console.print(metrics.snapshot())
