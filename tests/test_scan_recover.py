"""Hàng quét kq2 không bị chôn vì login wall / proxy chết."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from fb_poller.config import Settings
from fb_poller.storage.db import init_db, session_scope
from fb_poller.storage.models import Post, PostStatus
from fb_poller.storage.repo import PostRepo


class ScanRecoverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "t.db"
        self.settings = Settings(
            database_url=f"sqlite+aiosqlite:///{db}",
            hide_after_fail_streak=8,
            fail_streak_cooldown=3,
        )
        await init_db(self.settings)

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def _add_post(self) -> int:
        async with session_scope() as session:
            post = Post(url="https://facebook.com/x/posts/1")
            session.add(post)
            await session.flush()
            return post.id

    async def test_first_login_wall_keeps_post_active(self) -> None:
        post_id = await self._add_post()
        async with session_scope() as session:
            post = await session.get(Post, post_id)
            await PostRepo(session, self.settings).apply_poll_result(
                post,
                ok=False,
                inserted_new=0,
                fetched=0,
                error_code="login_wall",
                error_detail="login_wall_or_empty",
                new_ids=[],
                newest_time=None,
                guest_visible=False,
            )
        async with session_scope() as session:
            post = await session.get(Post, post_id)
            self.assertEqual(post.status, PostStatus.active.value)
            self.assertEqual(post.fail_streak, 1)
            self.assertNotEqual(post.guest_visible, False)

    async def test_hide_only_after_many_login_walls(self) -> None:
        post_id = await self._add_post()
        for _ in range(8):
            async with session_scope() as session:
                post = await session.get(Post, post_id)
                await PostRepo(session, self.settings).apply_poll_result(
                    post,
                    ok=False,
                    inserted_new=0,
                    fetched=0,
                    error_code="login_wall",
                    error_detail="login_wall_or_empty",
                    new_ids=[],
                    newest_time=None,
                    guest_visible=False,
                )
        async with session_scope() as session:
            post = await session.get(Post, post_id)
            self.assertEqual(post.status, PostStatus.guest_hidden.value)

    async def test_recover_brings_hidden_and_failed_back(self) -> None:
        async with session_scope() as session:
            hidden = Post(
                url="https://facebook.com/x/posts/hidden",
                status=PostStatus.guest_hidden.value,
                guest_visible=False,
                fail_streak=6,
            )
            stalled = Post(
                url="https://facebook.com/x/posts/stalled",
                status=PostStatus.active.value,
                fail_streak=4,
            )
            session.add_all([hidden, stalled])
        async with session_scope() as session:
            result = await PostRepo(session, self.settings).recover_scan_pool()
        self.assertEqual(result["recovered"], 1)
        self.assertEqual(result["reset_fail"], 1)
        async with session_scope() as session:
            rows = list((await session.scalars(select(Post))).all())
            statuses = {p.url: (p.status, p.fail_streak) for p in rows}
        self.assertEqual(statuses["https://facebook.com/x/posts/hidden"][0], PostStatus.active.value)
        self.assertEqual(statuses["https://facebook.com/x/posts/hidden"][1], 0)
        self.assertEqual(statuses["https://facebook.com/x/posts/stalled"][1], 0)
