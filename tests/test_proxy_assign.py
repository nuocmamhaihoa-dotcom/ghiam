"""Quét kq2 ưu tiên proxy 4G, thừa worker thì dùng chung 4G."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fb_poller.config import Settings
from fb_poller.storage.db import init_db, session_scope
from fb_poller.storage.repo import ProxyRepo
from fb_poller.workers.proxy_manager import parse_proxy_line


class AssignForScanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "t.db"
        self.settings = Settings(database_url=f"sqlite+aiosqlite:///{db}")
        await init_db(self.settings)

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_prefers_4g_over_static(self) -> None:
        async with session_scope() as session:
            repo = ProxyRepo(session)
            await repo.upsert_endpoints(["10.0.0.1:1:u:p", "10.0.0.2:2:u:p"], "static")
            await repo.upsert_endpoints(
                [
                    "103.125.189.149:8624:9rnbo:6wohm",
                    "103.122.162.163:8236:zec1z:vq8j2",
                ],
                "4g",
            )
            got = await repo.assign_for_scan("kq2", 2)
        self.assertEqual(len(got), 2)
        self.assertTrue(all(p.proxy_type == "4g" for p in got))
        self.assertEqual({p.endpoint for p in got}, {
            "103.125.189.149:8624:9rnbo:6wohm",
            "103.122.162.163:8236:zec1z:vq8j2",
        })

    async def test_shares_4g_when_more_workers_than_keys(self) -> None:
        async with session_scope() as session:
            repo = ProxyRepo(session)
            await repo.upsert_endpoints(["1.1.1.1:1:u:p", "1.1.1.2:2:u:p"], "4g")
            got = await repo.assign_for_scan("kq2", 5)
        self.assertEqual(len(got), 5)
        self.assertTrue(all(p.proxy_type == "4g" for p in got))
        self.assertEqual(len({p.endpoint for p in got}), 2)

    async def test_falls_back_to_static_without_4g(self) -> None:
        async with session_scope() as session:
            repo = ProxyRepo(session)
            await repo.upsert_endpoints(["8.8.8.8:1"], "static")
            got = await repo.assign_for_scan("kq2", 1)
        self.assertEqual(got[0].endpoint, "8.8.8.8:1")
        self.assertEqual(got[0].proxy_type, "static")

    async def test_reimport_revives_sick_4g(self) -> None:
        async with session_scope() as session:
            repo = ProxyRepo(session)
            await repo.upsert_endpoints(["103.125.189.149:8624:9rnbo:6wohm"], "4g")
            got = await repo.assign_for_scan("kq2", 1)
            await repo.mark_sick(got[0].id)
        async with session_scope() as session:
            repo = ProxyRepo(session)
            await repo.upsert_endpoints(["103.125.189.149:8624:9rnbo:6wohm"], "4g")
            got = await repo.assign_for_scan("kq2", 1)
        self.assertEqual(got[0].health, "ok")
        self.assertEqual(got[0].proxy_type, "4g")

    def test_parse_4g_user_pass(self) -> None:
        parsed = parse_proxy_line("103.125.189.149:8624:9rnbo:6wohm")
        assert parsed is not None
        self.assertEqual(parsed.server, "http://103.125.189.149:8624")
        self.assertEqual(parsed.username, "9rnbo")
        self.assertEqual(parsed.password, "6wohm")


if __name__ == "__main__":
    unittest.main()
