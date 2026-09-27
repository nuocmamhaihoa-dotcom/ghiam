from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import Certs, make_certs


@pytest.fixture(autouse=True)
def _empty_working_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent đọc config.json ở thư mục hiện tại; test không được dính file cấu hình thật của máy dev."""
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)


@pytest.fixture(scope="session")
def certs(tmp_path_factory: pytest.TempPathFactory) -> Certs:
    return make_certs(tmp_path_factory.mktemp("certs"))
