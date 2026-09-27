"""Remember operator actions locally, and mirror them to the hub when configured."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_LOCAL = 2000
_PRUNE_BYTES = 400_000


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def journal_path() -> Path:
    raw = os.environ.get("DATA_DIR", "").strip()
    base = Path(raw) if raw else ROOT / "data"
    return base / "operator_actions.jsonl"


def actor_name() -> str:
    return (
        os.environ.get("WORKER_ID")
        or os.environ.get("MACHINE_ID")
        or socket.gethostname()
        or "me"
    )[:80]


def redact_argv(argv: list[str]) -> str:
    """Join a CLI argv, hiding values that follow secret flags."""
    secret_flags = {"--token", "--control-token"}
    out: list[str] = []
    hide_next = False
    for arg in argv:
        if hide_next:
            out.append("***")
            hide_next = False
            continue
        if arg in secret_flags:
            out.append(arg)
            hide_next = True
            continue
        if arg.startswith("--token=") or arg.startswith("--control-token="):
            flag = arg.split("=", 1)[0]
            out.append(f"{flag}=***")
            continue
        out.append(arg)
    return " ".join(out)


def remember(
    *,
    summary: str,
    kind: str = "note",
    detail: str | None = None,
    source: str = "manual",
    actor: str | None = None,
    path: Path | None = None,
    push: bool = True,
) -> dict[str, Any]:
    text = " ".join(summary.split())
    if not text:
        raise ValueError("summary is empty")
    entry: dict[str, Any] = {
        "at": utcnow(),
        "actor": (actor or actor_name())[:80],
        "source": source[:40] or "manual",
        "kind": kind[:40] or "note",
        "summary": text[:500],
        "detail": detail[:2000] if detail else None,
    }
    dest = path or journal_path()
    _append(dest, entry)
    pushed = _push_remote(entry) if push else False
    return {**entry, "pushed": pushed}


def remember_quietly(**kwargs: Any) -> dict[str, Any] | None:
    """Record an action without letting a journal failure stop the real command."""
    try:
        return remember(**kwargs)
    except Exception:
        return None


def list_local(
    *,
    q: str = "",
    limit: int = 50,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    dest = path or journal_path()
    if not dest.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in dest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    needle = q.strip().lower()
    if needle:
        rows = [row for row in rows if _matches(row, needle)]
    limit = max(1, min(int(limit), 200))
    return list(reversed(rows[-limit:] if not needle else rows))[:limit]


def _matches(row: dict[str, Any], needle: str) -> bool:
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("summary", "detail", "kind", "actor", "source")
    ).lower()
    return needle in blob


def _append(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    try:
        if path.stat().st_size <= _PRUNE_BYTES:
            return
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        path.write_text("\n".join(lines[-MAX_LOCAL:]) + "\n", encoding="utf-8")
    except OSError:
        return


def _push_remote(entry: dict[str, Any]) -> bool:
    base = os.environ.get("CONTROL_URL", "").strip().rstrip("/")
    if not base:
        return False
    payload = json.dumps(
        {
            "summary": entry["summary"],
            "kind": entry["kind"],
            "detail": entry["detail"],
            "source": entry["source"],
            "actor": entry["actor"],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("CONTROL_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        base + "/v1/actions",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            response.read()
        return 200 <= int(response.status) < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
