"""Cổng 80 chuyển tiếp tới hub, và vòng kiểm tra để systemd khởi động lại khi hub im."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request

_CHUNK = 64 * 1024


def start_door(listen_port: int, target_port: int, host: str = "0.0.0.0") -> socket.socket | None:
    """Mở listen_port và chuyển byte sang 127.0.0.1:target_port. Trả về socket đang nghe."""
    if listen_port <= 0 or listen_port == target_port:
        return None
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((host, listen_port))
    except OSError as exc:
        server.close()
        print(f"Không mở được cổng {listen_port}: {exc}", file=sys.stderr)
        return None
    server.listen(128)
    threading.Thread(
        target=_accept,
        args=(server, target_port),
        name=f"door-{listen_port}",
        daemon=True,
    ).start()
    print(f"Cổng {listen_port} chuyển tiếp tới 127.0.0.1:{target_port}", file=sys.stderr)
    return server


def start_watchdog(port: int, *, grace_sec: float = 45, interval_sec: float = 20, misses_before_exit: int = 3) -> None:
    """Nếu /health im vài lần liên tiếp thì thoát để systemd kéo lại."""
    threading.Thread(
        target=_watch,
        args=(port, grace_sec, interval_sec, misses_before_exit),
        name="hub-watchdog",
        daemon=True,
    ).start()


def health_ok(port: int, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            return response.status == 200
    except (OSError, TimeoutError):
        return False


def _accept(server: socket.socket, target_port: int) -> None:
    while True:
        try:
            client, _addr = server.accept()
        except OSError:
            return
        threading.Thread(target=_bridge, args=(client, target_port), daemon=True).start()


def _bridge(client: socket.socket, target_port: int) -> None:
    upstream: socket.socket | None = None
    try:
        client.settimeout(120)
        upstream = socket.create_connection(("127.0.0.1", target_port), timeout=5)
        upstream.settimeout(120)
        left = threading.Thread(target=_pipe, args=(client, upstream), daemon=True)
        right = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
        left.start()
        right.start()
        left.join()
        right.join()
    except OSError:
        _close(client)
        if upstream is not None:
            _close(upstream)


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(_CHUNK)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                sent = dst.send(view)
                view = view[sent:]
    except OSError:
        pass
    finally:
        _close(src)
        _close(dst)


def _close(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _watch(port: int, grace_sec: float, interval_sec: float, misses_before_exit: int) -> None:
    time.sleep(grace_sec)
    misses = 0
    while True:
        if health_ok(port):
            misses = 0
        else:
            misses += 1
            print(f"Hub im lần {misses}/{misses_before_exit}", file=sys.stderr)
            if misses >= misses_before_exit:
                os._exit(75)
        time.sleep(interval_sec)
