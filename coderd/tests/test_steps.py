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
from app import db as appdb
from worker.agent import normalize_reply, parse_text_tools, run_turn, scripted_complete, system_prompt
from worker.study import extract_notes, ingest_sources, merge_lessons, parse_lessons, study_once  # noqa: E402
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
        self.assertIn("Đoạn chat mới", page.text)
        self.assertIn('id="composer"', page.text)
        self.assertNotIn("user-token", page.text)

    def test_home_cookie_opens_the_api(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.cookies.get("coderd_token"), "user-token")
        opened = self.client.get("/v1/sessions")
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.json(), [])

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
        sessions = again.get("/v1/sessions", headers=AUTH)
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(sessions.json()[0]["title"], "xin chào")

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
            self.assertEqual(nxt["history"][0]["content"], "một")
            self.assertIn("xong một", nxt["history"][1]["content"])

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

    def test_prior_dialogue_stops_before_current_message(self) -> None:
        session = self.client.post("/v1/sessions", headers=AUTH).json()
        first = self.client.post(
            f"/v1/sessions/{session['id']}/messages",
            headers=AUTH,
            json={"text": "câu trước"},
        ).json()
        second = self.client.post(
            f"/v1/sessions/{session['id']}/messages",
            headers=AUTH,
            json={"text": "câu sau"},
        ).json()
        appdb.add_event(Path(os.environ["CODERD_DB"]), first["id"], "text", "đã trả lời")
        dialogue = appdb.prior_dialogue(Path(os.environ["CODERD_DB"]), session["id"], second["id"])
        self.assertEqual(dialogue[0]["content"], "câu trước")
        self.assertEqual(dialogue[1]["content"], "đã trả lời")


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
        def always(messages, _tools):
            step = sum(1 for item in messages if item.get("role") == "tool")
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "x",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": json.dumps({"path": str(step)})},
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

    def test_repeated_tool_stops_with_stdout(self) -> None:
        def complete(messages, _tools):
            if not any(item.get("role") == "tool" for item in messages):
                return {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "w",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "hello.py", "content": "print(2 + 2)\n"}),
                            },
                        }
                    ],
                }
            return {
                "content": '{"name": "run_cmd", "arguments": {"command": "python3 hello.py"}}',
                "tool_calls": [
                    {
                        "id": "r",
                        "type": "function",
                        "function": {"name": "run_cmd", "arguments": json.dumps({"command": "python3 hello.py"})},
                    }
                ],
            }

        with TemporaryDirectory() as tmp:
            events: list[tuple[str, str]] = []
            run_turn(
                Workspace(Path(tmp) / "work"),
                "chạy",
                complete,
                lambda kind, payload: events.append((kind, payload)),
                threading.Event(),
                {},
            )
            self.assertEqual(events[-1][0], "done")
            self.assertLess(sum(1 for kind, _payload in events if kind == "tool"), 6)
            self.assertTrue(any(kind == "text" and "4" in payload for kind, payload in events))

    def test_study_keeps_a_lesson_once(self) -> None:
        self.assertEqual(parse_lessons("- không có\n- Có\n- 4\n- 2 cộng 2 bằng 4\n- 2 cộng 2 bằng 4"), ["2 cộng 2 bằng 4"])
        self.assertIn("phép cộng", merge_lessons("- phép cộng\n", ["phép cộng", "in ra 4"]))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "coderd.db"
            appdb.init_db(database)
            session = appdb.create_session(database)
            message = appdb.add_message(database, session["id"], "2+2")
            appdb.set_message_state(database, message["id"], "done")
            appdb.add_event(database, message["id"], "text", "bằng 4")

            def ask(_prompt: str) -> str:
                return "- 2 cộng 2 bằng 4"

            self.assertEqual(study_once(database, root / "work", ask), 1)
            lesson = (root / "work" / "learn" / "kinh-nghiem.md").read_text(encoding="utf-8")
            self.assertIn("2 cộng 2 bằng 4", lesson)
            self.assertEqual(study_once(database, root / "work", ask), 0)
            self.assertIn("OnUnitActiveSec=3min", (ROOT / "deploy" / "coderd-learn.timer").read_text(encoding="utf-8"))

    def test_ingest_saves_local_and_open_sources(self) -> None:
        sample = (
            "In the following examples, input and output are distinguished by the presence or\n"
            "Comments in Python start with the hash character.\n"
        )
        notes = extract_notes(sample, limit=4)
        self.assertTrue(any("hash character" in note for note in notes))
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "work"
            learn = root / "learn"
            learn.mkdir(parents=True)
            (learn / "python.md").write_text("- print in ra màn hình\n- int đổi chữ thành số\n", encoding="utf-8")

            def fetch(_url: str) -> str:
                return "Comments in Python start with the hash character, and extend to the end.\n"

            count = ingest_sources(root, fetch)
            saved = (learn / "da-hoc.md").read_text(encoding="utf-8")
            self.assertGreaterEqual(count, 2)
            self.assertIn("print in ra màn hình", saved)
            self.assertIn("hash character", saved)
            self.assertIn("python-nhap-mon", saved)
            short = (learn / "kinh-nghiem.md").read_text(encoding="utf-8")
            self.assertIn("print in ra màn hình", short)

    def test_text_tool_line_writes_a_file(self) -> None:
        def complete(messages, _tools):
            if any(item.get("role") == "tool" for item in messages):
                return {"content": "đã ghi", "tool_calls": []}
            return {
                "content": 'TOOL {"name":"write_file","arguments":{"path":"a.txt","content":"hi"}}',
                "tool_calls": [],
            }

        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "work"
            events: list[tuple[str, str]] = []
            run_turn(
                Workspace(root),
                "ghi file",
                complete,
                lambda kind, payload: events.append((kind, payload)),
                threading.Event(),
                {},
            )
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "hi")
            self.assertEqual(events[-1], ("done", ""))
            shown = [payload for kind, payload in events if kind == "text"]
            self.assertEqual(shown, ["đã ghi"])

    def test_parse_text_tools_keeps_normal_lines(self) -> None:
        cleaned, calls = parse_text_tools('xin chào\nTOOL {"name":"list_dir","arguments":{"path":"."}}')
        self.assertEqual(cleaned, "xin chào")
        self.assertEqual(calls[0]["function"]["name"], "list_dir")
        plain = normalize_reply({"content": "chỉ nói", "tool_calls": []})
        self.assertEqual(plain["content"], "chỉ nói")
        self.assertEqual(plain["tool_calls"], [])
        echoed = normalize_reply(
            {"content": '{"exit_code": 0, "stdout": "4\\n", "stderr": ""}', "tool_calls": []}
        )
        self.assertEqual(echoed["content"], "4")
        self.assertTrue((ROOT / "learn" / "muc-luc.md").is_file())
        self.assertTrue((ROOT / "learn" / "nguon-api.md").is_file())
        self.assertIn("nguon-api.md", (ROOT / "learn" / "muc-luc.md").read_text(encoding="utf-8"))
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "work"
            workspace = Workspace(root)
            workspace.ensure()
            (root / "learn").mkdir()
            (root / "learn" / "kinh-nghiem.md").write_text("- đã biết phép cộng\n", encoding="utf-8")
            self.assertIn("đã biết phép cộng", system_prompt(workspace))
        junk = normalize_reply({"content": '{"truncated": false}', "tool_calls": []})
        self.assertIsNone(junk["content"])


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
        subprocess.run(["bash", "-n", str(ROOT / "deploy" / "install-systemd.sh")], check=True)
        subprocess.run(["bash", "-n", str(ROOT / "deploy" / "install-model.sh")], check=True)
        subprocess.run(["bash", "-n", str(ROOT / "worker" / "install-linux.sh")], check=True)

    def test_systemd_install_serves_health_and_session(self) -> None:
        import urllib.error

        with TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "coderd"
            env = os.environ.copy()
            env["CODERD_PREFIX"] = str(prefix)
            env["APP_HOST"] = "127.0.0.1"
            env["APP_PORT"] = "8091"
            env["CODERD_START"] = "0"
            env["CODERD_SKIP_SYSTEMD"] = "1"
            subprocess.run(["bash", str(ROOT / "deploy" / "install-systemd.sh")], cwd=ROOT, env=env, check=True)
            env_file = prefix / "coderd.env"
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
            values = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertGreater(len(values["AUTH_TOKEN"]), 8)
            self.assertGreater(len(values["WORKER_TOKEN"]), 8)
            unit = (prefix / "coderd.service").read_text(encoding="utf-8")
            self.assertIn("Restart=always", unit)
            self.assertIn("python -m app", unit)
            self.assertNotIn("8088", unit)
            proc_env = os.environ.copy()
            proc_env.update(values)
            proc = subprocess.Popen(
                [str(prefix / ".venv" / "bin" / "python"), "-m", "app"],
                cwd=prefix,
                env=proc_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                base = "http://127.0.0.1:8091"
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(base + "/health", timeout=0.5) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        time.sleep(0.1)
                else:
                    self.fail("bản cài không lên")
                opener = urllib.request.Request(
                    base + "/v1/sessions",
                    method="POST",
                    headers={"Authorization": "Bearer " + values["AUTH_TOKEN"]},
                )
                with urllib.request.urlopen(opener, timeout=5) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.assertTrue(body["id"])
                denied = urllib.request.Request(base + "/v1/sessions", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(denied, timeout=5)
                self.assertEqual(caught.exception.code, 401)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

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

    def test_missing_model_returns_an_error(self) -> None:
        env = self.env.copy()
        env.pop("CODERD_SCRIPTED", None)
        env.pop("MODEL_BASE_URL", None)
        env["CODERD_URL"] = self.base
        env["CODERD_HOSTNAME"] = "pc-test"
        env["WORKSPACE_ROOT"] = str(self.root / "workspace")
        env["PYTHONUNBUFFERED"] = "1"
        self.worker_log = open(self.root / "worker.err", "w", encoding="utf-8")
        worker = subprocess.Popen(
            [sys.executable, "-m", "worker.connect"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self.worker_log,
        )
        try:
            self.wait_until(lambda: self.online())
            session = self.request("POST", "/v1/sessions")
            self.request("POST", f"/v1/sessions/{session['id']}/messages", {"text": "xin chào"})

            def failed():
                messages = self.request("GET", f"/v1/sessions/{session['id']}/messages")
                if messages and messages[0]["state"] == "error":
                    return messages[0]
                return None

            message = self.wait_until(failed, timeout=10)
            errors = [event["payload"] for event in message["events"] if event["kind"] == "error"]
            self.assertTrue(any("MODEL_BASE_URL" in item for item in errors))
        finally:
            self.stop(worker)

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
