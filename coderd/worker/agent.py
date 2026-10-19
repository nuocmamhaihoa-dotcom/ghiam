from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

from worker.tools import run_tool, schemas
from worker.workspace import Workspace


def scripted_complete(messages: list[dict], _tools: list[dict]) -> dict:
    user = str(messages[0].get("content") or "")
    tool_count = sum(1 for item in messages if item.get("role") == "tool")
    if "sleep" in user:
        if tool_count == 0:
            return _call("run_cmd", {"command": "sleep 60"}, "call_sleep")
        return {"content": "đã dừng", "tool_calls": []}
    if tool_count == 0:
        return _call("write_file", {"path": "hello.py", "content": "print(2 + 2)\n"}, "call_write")
    if tool_count == 1:
        return _call("run_cmd", {"command": "python3 hello.py"}, "call_run")
    return {"content": "stdout là 4", "tool_calls": []}


def _call(name: str, args: dict, call_id: str) -> dict:
    return {
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def openai_complete(messages: list[dict], tools: list[dict]) -> dict:
    base = os.environ["MODEL_BASE_URL"].rstrip("/")
    model = os.environ["MODEL_NAME"]
    key = os.environ.get("MODEL_API_KEY", "")
    payload = {"model": model, "messages": messages, "tools": tools}
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"model trả {exc.code}: {detail}") from exc
    message = data["choices"][0]["message"]
    return {"content": message.get("content"), "tool_calls": message.get("tool_calls") or []}


def complete_from_env():
    if os.environ.get("MODEL_BASE_URL"):
        return openai_complete
    if os.environ.get("CODERD_SCRIPTED") == "1":
        return scripted_complete
    raise RuntimeError("chưa cấu hình MODEL_BASE_URL")


def run_turn(
    workspace: Workspace,
    text: str,
    complete,
    emit,
    cancel: threading.Event,
    procs: dict,
    before_terminal=None,
) -> None:
    workspace.ensure()

    def finish(kind: str, payload: str) -> None:
        if before_terminal is not None:
            before_terminal()
        emit(kind, payload)

    messages: list[dict] = [{"role": "user", "content": text}]
    tool_schema = schemas()
    for _round in range(20):
        if cancel.is_set():
            finish("cancelled", "")
            return
        reply = complete(messages, tool_schema)
        content = reply.get("content")
        calls = reply.get("tool_calls") or []
        if content:
            emit("text", str(content))
        if not calls:
            finish("done", "")
            return
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for call in calls:
            if cancel.is_set():
                finish("cancelled", "")
                return
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            result = run_tool(workspace, str(function.get("name") or ""), args, cancel, procs)
            emit("tool", json.dumps({"name": function.get("name"), "result": result}, ensure_ascii=False))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            if result.get("cancelled"):
                finish("cancelled", "")
                return
    finish("error", "hết 20 vòng")
