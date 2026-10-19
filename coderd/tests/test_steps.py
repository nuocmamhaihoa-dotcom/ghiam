from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AUTH_TOKEN", "user-token")
os.environ.setdefault("WORKER_TOKEN", "worker-token")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from worker.agent import run_turn, scripted_complete  # noqa: E402
from worker.tools import run_cmd, run_tool  # noqa: E402
from worker.workspace import Workspace  # noqa: E402

AUTH = {"Authorization": "Bearer user-token"}


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["CODERD_DB"] = str(self.root / "coderd.db")
        os.environ["GIT_BARE"] = str(self.root / "bare.git")
        os.environ["AUTH_TOKEN"] = "user-token"
        os.environ["WORKER_TOKEN"] = "worker-token"
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_health_is_public_and_api_requires_token(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"ok": True})
        denied = self.client.post("/v1/sessions")
        self.assertEqual(denied.status_code, 401)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Phiên mới", page.text)
        self.assertIn('id="stop"', page.text)

    def test_message_stays_queued_across_restart(self) -> None:
        session = self.client.post("/v1/sessions", headers=AUTH).json()
        message = self.client.post(
            f"/v1/sessions/{session['id']}/messages",
            headers=AUTH,
            json={"text": "xin chào"},
        ).json()
        self.assertEqual(message["state"], "queued")
        again = TestClient(create_app())
        listed = again.get(f"/v1/sessions/{session['id']}/messages", headers=AUTH)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()[0]["body"], "xin chào")
        self.assertEqual(listed.json()[0]["state"], "queued")

    def hello(self, name: str = "pc") -> dict:
        return {
            "type": "hello",
            "token": "worker-token",
            "hostname": name,
            "cpu": 4,
            "ram": 8_000_000_000,
            "ram_free": 4_000_000_000,
        }

    def test_second_worker_is_rejected_and_reconnect_keeps_one_row(self) -> None:
        with self.client.websocket_connect("/v1/workers/connect") as first:
            first.send_json(self.hello("pc"))
            self.assertEqual(first.receive_json()["type"], "hello_ok")
            with self.client.websocket_connect("/v1/workers/connect") as second:
                second.send_json(self.hello("pc"))
                rejected = second.receive_json()
                self.assertEqual(rejected["reason"], "already-attached")
            workers = self.client.get("/v1/workers", headers=AUTH).json()
            self.assertEqual(len(workers), 1)
        with self.client.websocket_connect("/v1/workers/connect") as third:
            third.send_json(self.hello("pc"))
            self.assertEqual(third.receive_json()["type"], "hello_ok")
            workers = self.client.get("/v1/workers", headers=AUTH).json()
            self.assertEqual(len(workers), 1)
            self.assertEqual(workers[0]["status"], "online")

    def test_stale_or_detached_worker_is_offline(self) -> None:
        with self.client.websocket_connect("/v1/workers/connect") as socket:
            socket.send_json(self.hello("pc"))
            socket.receive_json()
        workers = self.client.get("/v1/workers", headers=AUTH).json()
        self.assertEqual(workers[0]["status"], "offline")
        old = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="seconds")
        with sqlite3.connect(os.environ["CODERD_DB"]) as conn:
            conn.execute("UPDATE workers SET attached = 1, last_seen = ?", (old,))
        workers = self.client.get("/v1/workers", headers=AUTH).json()
        self.assertEqual(workers[0]["status"], "offline")

    def test_offline_message_waits_then_runs_one_at_a_time(self) -> None:
        session = self.client.post("/v1/sessions", headers=AUTH).json()
        first = self.client.post(
            f"/v1/sessions/{session['id']}/messages",
            headers=AUTH,
            json={"text": "một"},
        ).json()
        second = self.client.post(
            f"/v1/sessions/{session['id']}/messages",
            headers=AUTH,
            json={"text": "hai"},
        ).json()
        self.assertEqual(first["state"], "queued")
        self.assertEqual(second["state"], "queued")
        with self.client.websocket_connect("/v1/workers/connect") as socket:
            socket.send_json(self.hello())
            socket.receive_json()
            assigned = socket.receive_json()
            self.assertEqual(assigned["text"], "một")
            listed = self.client.get(f"/v1/sessions/{session['id']}/messages", headers=AUTH).json()
            states = {item["body"]: item["state"] for item in listed}
            self.assertEqual(states["một"], "running")
            self.assertEqual(states["hai"], "queued")
            socket.send_json({"type": "event", "message_id": assigned["message_id"], "kind": "text", "payload": "xong một"})
            socket.send_json({"type": "event", "message_id": assigned["message_id"], "kind": "done", "payload": ""})
            nxt = socket.receive_json()
            self.assertEqual(nxt["text"], "hai")

    def test_cancel_queued_message(self) -> None:
        session = self.client.post("/v1/sessions", headers=AUTH).json()
        message = self.client.post(
            f"/v1/sessions/{session['id']}/messages",
            headers=AUTH,
            json={"text": "dừng"},
        ).json()
        cancelled = self.client.post(f"/v1/messages/{message['id']}/cancel", headers=AUTH)
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "cancelled")


class WorkerTests(unittest.TestCase):
    def test_path_stays_inside_workspace(self) -> None:
        with TemporaryDirectory() as tmp:
            workspace = Workspace(Path(tmp) / "work")
            workspace.ensure()
            with self.assertRaises(PermissionError):
                workspace.resolve("../ngoài")
            with self.assertRaises(PermissionError):
                workspace.resolve("/etc/passwd")
            denied = run_tool(workspace, "write_file", {"path": "../x", "content": "a"}, threading.Event(), {})
            self.assertIn("error", denied)

    def test_scripted_hello_prints_4(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "work"
            events: list[tuple[str, str]] = []
            run_turn(
                Workspace(root),
                "tạo hello.py in 2+2 rồi chạy",
                scripted_complete,
                lambda kind, payload: events.append((kind, payload)),
                threading.Event(),
                {},
            )
            self.assertEqual((root / "hello.py").read_text(encoding="utf-8"), "print(2 + 2)\n")
            tools = [json.loads(payload) for kind, payload in events if kind == "tool"]
            self.assertEqual(tools[1]["result"]["stdout"].strip(), "4")
            self.assertEqual(events[-1][0], "done")

    def test_cancel_stops_sleep(self) -> None:
        with TemporaryDirectory() as tmp:
            cancel = threading.Event()
            threading.Timer(0.2, cancel.set).start()
            started = time.monotonic()
            result = run_cmd("sleep 60", Path(tmp), cancel, {})
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(result.get("cancelled"))

    def test_command_timeout(self) -> None:
        with TemporaryDirectory() as tmp:
            started = time.monotonic()
            result = run_cmd("sleep 30", Path(tmp), threading.Event(), {}, timeout=0.4)
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(result["error"], "quá thời gian")

    def test_stops_after_20_rounds(self) -> None:
        def always(_messages, _tools):
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "x",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": "{\"path\": \".\"}"},
                    }
                ],
            }

        with TemporaryDirectory() as tmp:
            events: list[tuple[str, str]] = []
            run_turn(
                Workspace(Path(tmp) / "work"),
                "mãi",
                always,
                lambda kind, payload: events.append((kind, payload)),
                threading.Event(),
                {},
            )
            self.assertEqual(events[-1], ("error", "hết 20 vòng"))


class DeployTests(unittest.TestCase):
    def test_backup_keeps_seven_copies(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "coderd.db"
            database.write_text("db", encoding="utf-8")
            git_dir = root / "repo.git"
            git_dir.mkdir()
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            dest = root / "backups"
            script = ROOT / "deploy" / "backup.sh"
            for _ in range(8):
                subprocess.run(["bash", str(script), str(database), str(git_dir), str(dest)], check=True)
            copies = [item for item in dest.iterdir() if item.is_dir()]
            self.assertEqual(len(copies), 7)
            newest = max(copies, key=lambda item: item.name)
            self.assertEqual((newest / "coderd.db").read_text(encoding="utf-8"), "db")

    def test_compose_restarts_and_scripts_parse(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(compose.count("unless-stopped"), 2)
        subprocess.run(["bash", "-n", str(ROOT / "deploy" / "backup.sh")], check=True)
        subprocess.run(["bash", "-n", str(ROOT / "deploy" / "install-vps.sh")], check=True)
        subprocess.run(["bash", "-n", str(ROOT / "worker" / "install-linux.sh")], check=True)

    def test_linux_install_writes_user_service(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            prefix = Path(tmp) / "prefix"
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["CODERD_PREFIX"] = str(prefix)
            env["CODERD_SKIP_PIP"] = "1"
            env["CODERD_INSTALL_ENABLE"] = "0"
            subprocess.run(["bash", str(ROOT / "worker" / "install-linux.sh")], cwd=ROOT, env=env, check=True)
            unit = (home / ".config" / "systemd" / "user" / "coderd-worker.service").read_text(encoding="utf-8")
            self.assertIn("Restart=on-failure", unit)
            self.assertIn(str(prefix), unit)
            mode = (home / ".config" / "coderd" / "worker.env").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)


class EndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.port = 8877
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = os.environ.copy()
        self.env["CODERD_DB"] = str(self.root / "coderd.db")
        self.env["GIT_BARE"] = str(self.root / "bare.git")
        self.env["AUTH_TOKEN"] = "user-token"
        self.env["WORKER_TOKEN"] = "worker-token"
        self.server = self.spawn_server()
        self.worker_log = None
        self.wait_health()

    def tearDown(self) -> None:
        self.stop(self.server)
        if self.worker_log is not None:
            self.worker_log.close()
        self.tmp.cleanup()

    def spawn_server(self) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=ROOT,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self, proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def wait_health(self) -> None:
        for _ in range(50):
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=0.5) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.1)
        raise AssertionError("server không lên")

    def request(self, method: str, path: str, payload: dict | None = None, token: str = "user-token") -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Authorization": f"Bearer {token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read()
        return json.loads(body) if body else {}

    def spawn_worker(self) -> subprocess.Popen:
        env = self.env.copy()
        env["CODERD_URL"] = self.base
        env["CODERD_SCRIPTED"] = "1"
        env["CODERD_HOSTNAME"] = "pc-test"
        env["WORKSPACE_ROOT"] = str(self.root / "workspace")
        env["PYTHONUNBUFFERED"] = "1"
        self.worker_log = open(self.root / "worker.err", "w", encoding="utf-8")
        return subprocess.Popen(
            [sys.executable, "-m", "worker.connect"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self.worker_log,
        )

    def wait_until(self, predicate, timeout: float = 20):
        started = time.monotonic()
        last = None
        while time.monotonic() - started < timeout:
            last = predicate()
            if last:
                return last
            time.sleep(0.2)
        log = ""
        err = self.root / "worker.err"
        if err.exists():
            log = err.read_text(encoding="utf-8")[-2000:]
        raise AssertionError(f"{last}\n{log}")

    def test_worker_runs_hello_and_mirrors_git(self) -> None:
        worker = self.spawn_worker()
        try:
            self.wait_until(lambda: self.online())
            session = self.request("POST", "/v1/sessions")
            self.request(
                "POST",
                f"/v1/sessions/{session['id']}/messages",
                {"text": "tạo hello.py in 2+2 rồi chạy"},
            )

            def finished():
                messages = self.request("GET", f"/v1/sessions/{session['id']}/messages")
                if messages and messages[0]["state"] == "done":
                    return messages[0]
                return None

            message = self.wait_until(finished)
            tools = [json.loads(event["payload"]) for event in message["events"] if event["kind"] == "tool"]
            runs = [item for item in tools if item["name"] == "run_cmd"]
            self.assertEqual(runs[0]["result"]["stdout"].strip(), "4")
            shown = subprocess.check_output(
                ["git", "--git-dir", self.env["GIT_BARE"], "show", "main:hello.py"],
                text=True,
            )
            self.assertIn("print(2 + 2)", shown)
            self.stop(self.server)
            self.server = None
            time.sleep(0.5)
            self.server = self.spawn_server()
            self.wait_health()
            workers = self.wait_until(lambda: self.online())
            self.assertEqual(len(workers), 1)
            self.assertEqual(workers[0]["hostname"], "pc-test")
            kept = self.request("GET", f"/v1/sessions/{session['id']}/messages")
            self.assertEqual(kept[0]["state"], "done")
        finally:
            self.stop(worker)

    def online(self):
        workers = self.request("GET", "/v1/workers")
        if workers and workers[0]["status"] == "online":
            return workers
        return None

    def test_cancel_sleep_from_the_page_api(self) -> None:
        worker = self.spawn_worker()
        try:
            self.wait_until(lambda: self.online())
            session = self.request("POST", "/v1/sessions")
            message = self.request("POST", f"/v1/sessions/{session['id']}/messages", {"text": "sleep"})
            started = time.monotonic()

            def running():
                current = self.request("GET", f"/v1/sessions/{session['id']}/messages")[0]
                return current if current["state"] == "running" else None

            self.wait_until(running, timeout=10)
            self.request("POST", f"/v1/messages/{message['id']}/cancel")

            def stopped():
                current = self.request("GET", f"/v1/sessions/{session['id']}/messages")[0]
                return current if current["state"] == "cancelled" else None

            self.wait_until(stopped, timeout=10)
            self.assertLess(time.monotonic() - started, 10)
        finally:
            self.stop(worker)


if __name__ == "__main__":
    unittest.main()
