from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import urllib.request
from pathlib import Path

from worker.agent import complete_from_env, run_turn
from worker.sync import publish
from worker.workspace import Workspace


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_env() -> None:
    named = os.environ.get("CODERD_ENV_FILE")
    if named:
        load_env_file(Path(named))
    load_env_file(Path.cwd() / "worker.env")
    load_env_file(Path.home() / ".config" / "coderd" / "worker.env")


def machine_stats() -> dict[str, int]:
    cpu = os.cpu_count() or 1
    ram = 0
    free = 0
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        info: dict[str, int] = {}
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.strip().split()[0]) * 1024
        ram = info.get("MemTotal", 0)
        free = info.get("MemAvailable", info.get("MemFree", 0))
    return {"cpu": cpu, "ram": ram, "ram_free": free}


def ws_url(base: str) -> str:
    base = base.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :] + "/v1/workers/connect"
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :] + "/v1/workers/connect"
    return base


def post_bundle(base: str, token: str, bundle: bytes) -> None:
    request = urllib.request.Request(
        base.rstrip("/") + "/v1/workspace/bundle",
        data=bundle,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        response.read()


async def run_once() -> None:
    import websockets

    base = os.environ.get("CODERD_URL", "http://127.0.0.1:8080")
    token = os.environ.get("WORKER_TOKEN", "")
    if not token:
        raise SystemExit("thiếu WORKER_TOKEN")
    hostname = os.environ.get("CODERD_HOSTNAME") or os.uname().nodename
    root = Path(os.environ.get("WORKSPACE_ROOT") or (Path.home() / "coderd-workspace"))
    workspace = Workspace(root)
    interval = float(os.environ.get("CODERD_HEARTBEAT_SEC", "15"))
    url = ws_url(base)
    async with websockets.connect(url) as socket:
        stats = machine_stats()
        await socket.send(json.dumps({"type": "hello", "token": token, "hostname": hostname, **stats}))
        hello = json.loads(await socket.recv())
        if hello.get("type") == "reject":
            reason = hello.get("reason")
            if reason == "unauthorized":
                raise SystemExit("WORKER_TOKEN không đúng")
            raise RuntimeError(reason or "từ chối")
        cancel = threading.Event()
        procs: dict = {"proc": None}
        busy = False
        pending: asyncio.Queue = asyncio.Queue()

        async def start(message_id: str, text: str) -> None:
            nonlocal busy, task
            busy = True
            cancel.clear()
            task = asyncio.create_task(work(message_id, text))

        async def beats() -> None:
            while True:
                await asyncio.sleep(interval)
                await socket.send(json.dumps({"type": "heartbeat", **machine_stats()}))

        async def work(message_id: str, text: str) -> None:
            nonlocal busy
            loop = asyncio.get_running_loop()

            def emit(kind: str, payload: str) -> None:
                body = json.dumps(
                    {"type": "event", "message_id": message_id, "kind": kind, "payload": payload},
                    ensure_ascii=False,
                )
                future = asyncio.run_coroutine_threadsafe(socket.send(body), loop)
                future.result(timeout=30)

            def before_terminal() -> None:
                if publish(workspace, lambda bundle: post_bundle(base, token, bundle)):
                    emit("tool", json.dumps({"name": "git", "result": {"pushed": True}}, ensure_ascii=False))

            try:
                await asyncio.to_thread(
                    run_turn,
                    workspace,
                    text,
                    complete_from_env(),
                    emit,
                    cancel,
                    procs,
                    before_terminal,
                )
            except Exception as exc:
                emit("error", str(exc)[:500])
            finally:
                busy = False
                cancel.clear()
                if not pending.empty():
                    incoming_next = pending.get_nowait()
                    await start(str(incoming_next.get("message_id")), str(incoming_next.get("text") or ""))

        beater = asyncio.create_task(beats())
        task: asyncio.Task | None = None
        try:
            while True:
                raw = await socket.recv()
                incoming = json.loads(raw)
                if incoming.get("type") == "assign":
                    if busy:
                        pending.put_nowait(incoming)
                    else:
                        await start(str(incoming.get("message_id")), str(incoming.get("text") or ""))
                elif incoming.get("type") == "cancel":
                    cancel.set()
                    proc = procs.get("proc")
                    if proc is not None and proc.poll() is None:
                        try:
                            import os as _os
                            import signal as _signal

                            _os.killpg(proc.pid, _signal.SIGKILL)
                        except ProcessLookupError:
                            pass
        finally:
            beater.cancel()
            if task is not None:
                cancel.set()
                await asyncio.gather(task, return_exceptions=True)


async def serve() -> None:
    delay = 1
    while True:
        try:
            await run_once()
            delay = 1
        except SystemExit:
            raise
        except Exception as exc:
            print(f"nối lại sau {delay}s: {exc}", file=sys.stderr)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)


def main() -> None:
    load_env()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
