"""Cấu hình agent: file JSON < biến môi trường COMMENTSCOPE_* < tham số dòng lệnh."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from commentscope_agent.config import AgentConfig, ConfigError, insecure_transport_warning, load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.json"
MINIMAL = {"COMMENTSCOPE_SERVER_URL": "https://scope.example.com", "COMMENTSCOPE_AGENT_TOKEN": "tok"}


def write_config(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_command_line_beats_environment_which_beats_the_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "agent.json",
        {"server_url": "https://file.example.com/", "token": "tu-file", "worker_id": "pc-file", "pool": "file"},
    )
    env = {"COMMENTSCOPE_AGENT_TOKEN": "  tu-env  ", "COMMENTSCOPE_POOL": "env", "COMMENTSCOPE_KIND": ""}
    config = load_config(path, env, {"pool": "cli", "worker_id": None})
    assert config == AgentConfig(server_url="https://file.example.com", token="tu-env", worker_id="pc-file", pool="cli")


def test_config_json_in_the_working_directory_is_used_by_default() -> None:
    write_config(Path("config.json"), {"server_url": "https://scope.example.com", "token": "tok", "kind": "rotating"})
    assert load_config(None, {}, {}).kind == "rotating"


def test_missing_default_file_is_fine_but_a_named_file_must_exist(tmp_path: Path) -> None:
    assert load_config(None, MINIMAL, {}).server_url == "https://scope.example.com"
    with pytest.raises(ConfigError, match="Không tìm thấy file cấu hình"):
        load_config(tmp_path / "khong-co.json", MINIMAL, {})


def test_file_saved_by_windows_notepad_with_bom_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"server_url": "https://scope.example.com", "token": "tok"}), encoding="utf-8-sig")
    assert load_config(path, {}, {}).token == "tok"


def test_example_config_only_uses_supported_keys() -> None:
    config = load_config(EXAMPLE, {}, {})
    assert config.server_url == "https://scope.example.com"
    assert config.kind is None


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{ server_url: 1 }", r"không phải JSON hợp lệ \(dòng 1, cột 3\)"),
        ("[1, 2]", "phải là một object JSON"),
        ('{"server": "https://x", "tokn": "t"}', "khoá không hỗ trợ: server, tokn"),
    ],
)
def test_broken_config_files_are_explained(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path, {}, {})


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"token": "tok"}, "Thiếu địa chỉ VPS"),
        ({"server_url": "https://scope.example.com"}, "COMMENTSCOPE_AGENT_TOKEN"),
        ({"server_url": "scope.example.com", "token": "tok"}, "phải bắt đầu bằng https://"),
        ({"kind": "4g"}, "static .* hoặc rotating"),
        ({"lease_ttl_sec": 10}, "từ 30 đến 86400"),
        ({"lease_ttl_sec": 90.5}, "số nguyên"),
        ({"lease_wait_sec": -1}, "không được âm"),
        ({"request_timeout_sec": 0}, "lớn hơn 0"),
        ({"request_timeout_sec": True}, "phải là số"),
        ({"check_url": "ftp://ip.example.com"}, "check_url"),
        ({"pool": 5}, "pool phải là chuỗi"),
        ({"worker_id": "x" * 129}, "128 ký tự"),
    ],
)
def test_invalid_values_are_explained(tmp_path: Path, values: dict[str, Any], message: str) -> None:
    base = {"server_url": "https://scope.example.com", "token": "tok"}
    has_required = "server_url" in values or "token" in values
    data = values if has_required else {**base, **values}
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path / "config.json", data), {}, {})


def test_token_is_hidden_from_repr() -> None:
    config = load_config(None, {**MINIMAL, "COMMENTSCOPE_AGENT_TOKEN": "rat-bi-mat"}, {})
    assert "rat-bi-mat" not in repr(config)


def test_worker_id_defaults_to_a_cleaned_up_computer_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "Máy PC #1.")
    assert load_config(None, MINIMAL, {}).worker_id == "M-y-PC-1"
    monkeypatch.setattr(socket, "gethostname", lambda: "...")
    assert load_config(None, MINIMAL, {}).worker_id == "pc"


@pytest.mark.parametrize(
    ("server_url", "warns"),
    [
        ("http://203.0.113.10:8000", True),
        ("http://scope.example.com", True),
        ("https://scope.example.com", False),
        ("http://127.0.0.1:8000", False),
        ("http://localhost:8000", False),
        ("http://[::1]:8000", False),
    ],
)
def test_warns_when_the_token_would_travel_unencrypted(server_url: str, warns: bool) -> None:
    config = load_config(None, {**MINIMAL, "COMMENTSCOPE_SERVER_URL": server_url}, {})
    assert (insecure_transport_warning(config) is not None) is warns
