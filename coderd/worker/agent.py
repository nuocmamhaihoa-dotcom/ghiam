from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

from worker.tools import run_tool, schemas
from worker.use import use_context, vietnamese_lessons
from worker.workspace import Workspace


SYSTEM = (
    "Bạn là coderd, trợ lý lập trình chạy trên máy của người dùng. "
    "Trả lời tiếng Việt, ngắn. "
    "Khi cần xem, ghi file hoặc chạy lệnh, in đúng một dòng và không thêm chữ khác:\n"
    'TOOL {"name":"write_file","arguments":{"path":"ten.py","content":"print(1)\\n"}}\n'
    "Tool hợp lệ: list_dir, read_file, write_file, run_cmd. "
    "Khi được bảo chạy lệnh, gọi run_cmd đúng một lần rồi trả lời tiếng Việt, không in JSON. "
    "Mọi path nằm trong workspace. "
    "Khi người dùng nói tiếng Việt và cần lập trình, đọc learn/dung.md rồi đúng một file trong learn/lenh/ và khi cần đúng một file trong learn/the/. "
    "Nhóm lệnh: python, git, shell, sqlite, mang, he-thong, docker, node, go, rust, java, bien-dich, csdl. "
    "Để nhớ một việc cho lần sau, ghi đè file .coderd/memory.md."
)


def scripted_complete(messages: list[dict], _tools: list[dict]) -> dict:
    user = ""
    for item in messages:
        if item.get("role") == "user":
            user = str(item.get("content") or "")
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


def _post_chat(messages: list[dict], tools: list[dict] | None) -> dict:
    base = os.environ["MODEL_BASE_URL"].rstrip("/")
    model = os.environ["MODEL_NAME"]
    key = os.environ.get("MODEL_API_KEY", "")
    payload: dict = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"model trả {exc.code}: {detail}") from exc
    message = data["choices"][0]["message"]
    return {"content": message.get("content"), "tool_calls": message.get("tool_calls") or []}


def openai_complete(messages: list[dict], tools: list[dict]) -> dict:
    try:
        return _post_chat(messages, tools)
    except RuntimeError as exc:
        if tools and ("tool" in str(exc).lower() or "400" in str(exc)):
            return _post_chat(messages, None)
        raise


def parse_text_tools(content: str) -> tuple[str, list[dict]]:
    calls: list[dict] = []
    kept: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        raw = ""
        if stripped.startswith("TOOL"):
            raw = stripped[4:].strip()
        elif stripped.startswith("{") and stripped.endswith("}") and '"name"' in stripped:
            raw = stripped
        if not raw:
            kept.append(line)
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        name = str(data.get("name") or "")
        args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        if not name:
            kept.append(line)
            continue
        calls.append(
            {
                "id": f"text_{len(calls)}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
        )
    cleaned = "\n".join(kept).strip()
    return cleaned, calls


def _visible_text(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and "stdout" in data:
            return str(data.get("stdout") or "").strip()
    kept: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("TOOL") or stripped.startswith("{") or '"arguments"' in stripped:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def normalize_reply(reply: dict) -> dict:
    content = reply.get("content")
    calls = list(reply.get("tool_calls") or [])
    if calls:
        visible = _visible_text(str(content or ""))
        return {"content": visible or None, "tool_calls": calls}
    if not content:
        return {"content": None, "tool_calls": []}
    cleaned, parsed = parse_text_tools(str(content))
    if not parsed:
        visible = _visible_text(str(content))
        if visible:
            return {"content": visible, "tool_calls": []}
        stripped = str(content).strip()
        if stripped.startswith("{") or stripped.startswith("TOOL"):
            return {"content": None, "tool_calls": []}
        return {"content": content, "tool_calls": []}
    return {"content": cleaned or None, "tool_calls": parsed}


def complete_from_env():
    if os.environ.get("MODEL_BASE_URL"):
        return openai_complete
    if os.environ.get("CODERD_SCRIPTED") == "1":
        return scripted_complete
    raise RuntimeError("chưa cấu hình MODEL_BASE_URL")


def system_prompt(workspace: Workspace, question: str = "") -> str:
    text = SYSTEM
    try:
        memory = workspace.resolve(".coderd/memory.md")
    except PermissionError:
        return text
    if memory.is_file():
        note = memory.read_text(encoding="utf-8", errors="replace")[:2000].strip()
        if note:
            text += "\nGhi nhớ:\n" + note
    try:
        lessons = workspace.resolve("learn/kinh-nghiem.md")
    except PermissionError:
        lessons = None
    else:
        if lessons.is_file():
            note = vietnamese_lessons(lessons.read_text(encoding="utf-8", errors="replace"))
            if note:
                text += "\nĐã học:\n" + note
    linked = use_context(workspace.root, question)
    if linked:
        text += "\nDùng ngay:\n" + linked
    return text


def run_turn(
    workspace: Workspace,
    text: str,
    complete,
    emit,
    cancel: threading.Event,
    procs: dict,
    before_terminal=None,
    history: list[dict] | None = None,
) -> None:
    workspace.ensure()

    def finish(kind: str, payload: str) -> None:
        if before_terminal is not None:
            before_terminal()
        emit(kind, payload)

    messages: list[dict] = [{"role": "system", "content": system_prompt(workspace, text)}]
    for item in history or []:
        role = item.get("role")
        content = str(item.get("content") or "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content[:2000]})
    messages.append({"role": "user", "content": text})
    tool_schema = schemas()
    seen: set[str] = set()
    last_stdout = ""
    said = False
    for _round in range(20):
        if cancel.is_set():
            finish("cancelled", "")
            return
        reply = normalize_reply(complete(messages, tool_schema))
        content = reply.get("content")
        calls = reply.get("tool_calls") or []
        if content:
            emit("text", str(content))
            said = True
        if not calls:
            if not said and last_stdout:
                emit("text", last_stdout)
            finish("done", "")
            return
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        repeated = False
        for call in calls:
            if cancel.is_set():
                finish("cancelled", "")
                return
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            name = str(function.get("name") or "")
            key = name + "\n" + json.dumps(args, sort_keys=True, ensure_ascii=False)
            if key in seen:
                repeated = True
                continue
            seen.add(key)
            result = run_tool(workspace, name, args, cancel, procs)
            stdout = str(result.get("stdout") or "").strip()
            if stdout:
                last_stdout = stdout
            emit("tool", json.dumps({"name": name, "result": result}, ensure_ascii=False))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "content": json.dumps(result, ensure_ascii=False)[:1500],
                }
            )
            if result.get("cancelled"):
                finish("cancelled", "")
                return
        if repeated:
            if not said and last_stdout:
                emit("text", last_stdout)
            finish("done", "")
            return
    finish("error", "hết 20 vòng")
