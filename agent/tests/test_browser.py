"""Dừng agent đúng lúc Chromium đang mở: cần driver Playwright (có sẵn trong gói pip), không cần Chromium."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from commentscope_agent.browser import ProxiedBrowser

async_api = pytest.importorskip("playwright.async_api")


@pytest.mark.parametrize("stage", ["driver", "launch"])
async def test_ctrl_c_while_chromium_opens_shuts_the_playwright_driver_down(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    reached = asyncio.Event()
    drivers: list[asyncio.subprocess.Process] = []
    spawn = asyncio.create_subprocess_exec

    async def spawn_driver(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await spawn(*args, **kwargs)
        drivers.append(process)
        if stage == "driver":
            reached.set()
        return process

    async def launch_forever(*_args: Any, **_kwargs: Any) -> None:
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_driver)
    monkeypatch.setattr(async_api.BrowserType, "launch", launch_forever)
    browser = ProxiedBrowser("http://127.0.0.1:9", username="agent", password="x")
    opening = asyncio.create_task(browser.__aenter__())
    await reached.wait()
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert [driver.returncode is not None for driver in drivers] == [True]
