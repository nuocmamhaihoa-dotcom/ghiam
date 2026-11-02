"""Launch the recorder window, or play a saved macro from the terminal."""

from __future__ import annotations

import argparse
import sys
import time
from typing import Sequence

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.events import default_title
from pc_macro.hub import HubError, describe, pull, push
from pc_macro.updater import apply_if_needed
from pc_macro.numbers import NumberQueue, apply_turn, digit_spans
from pc_macro.screen import RECOVER_CLICKS, capture_grid, current_screen_size, play_named_clicks, recover_after_enter, scale_clicks
from pc_macro.script import compile_script, events_to_steps, number_enter_steps, script_captions, steps_to_events
from pc_macro.session import Player
from pc_macro.store import MacroStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pc-macro",
        description="Ghi nhớ thao tác bàn phím và chuột trên máy, rồi làm lại.",
    )
    parser.add_argument(
        "--updated",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("gui", help="Mở cửa sổ Ghi / Làm lại")
    sub.add_parser("list", help="Liệt kê bản đã lưu")

    play = sub.add_parser("play", help="Làm lại một bản đã lưu")
    play.add_argument("name", help="Tên hoặc slug của bản ghi")
    play.add_argument("--repeat", type=int, default=1, help="Số lần. 0 = đến khi Ctrl+C")
    play.add_argument("--delay", type=float, default=2, help="Giây chờ trước khi bắt đầu")

    show = sub.add_parser("show", help="Xem các bước của một bản")
    show.add_argument("name")

    sub.add_parser("hub", help="Xem địa chỉ kho VPS đã đặt")

    remote_pull = sub.add_parser("pull", help="Lấy kịch bản từ VPS về máy")
    remote_pull.add_argument("name", nargs="?", help="Slug hoặc để trống để lấy hết")

    remote_push = sub.add_parser("push", help="Đưa kịch bản trên máy lên VPS")
    remote_push.add_argument("name", nargs="?", help="Slug hoặc để trống để đưa hết")

    updater = sub.add_parser("update", help="Tự tải bản mới từ hub rồi thoát")
    updater.add_argument("--then-gui", action="store_true", help="Mở cửa sổ sau khi cập nhật")

    args = parser.parse_args(argv)
    if args.cmd == "update":
        return _run_update(then_gui=bool(args.then_gui))
    if args.cmd in (None, "gui"):
        return _launch_gui(skip_update=bool(args.updated))
    store = MacroStore()
    if args.cmd == "hub":
        print(describe())
        return 0
    if args.cmd in {"pull", "push"}:
        return _sync(store, args.cmd, getattr(args, "name", None))
    if args.cmd == "list":
        items = store.list()
        if not items:
            print("Chưa có bản ghi. Chạy: python -m pc_macro")
            return 0
        for item in items:
            steps = item.get("step_count") or len(item.get("steps") or [])
            print(f"{item['slug']}\t{item['name']}\t{steps} bước")
        return 0
    item = store.get(args.name)
    if item is None:
        for row in store.list():
            if row["name"] == args.name:
                item = row
                break
    if item is None:
        print(f"Không thấy bản ghi «{args.name}».")
        return 1
    if args.cmd == "show":
        steps = item.get("steps") or events_to_steps(item["events"])
        print(item["name"] or default_title(item["events"]))
        for index, line in enumerate(script_captions(steps), start=1):
            print(f"{index}. {line}")
        return 0
    if not available():
        print(install_hint())
        return 1
    if args.delay > 0:
        print(f"Bắt đầu sau {args.delay:.0f} giây…")
        time.sleep(args.delay)
    desktop = Desktop(lambda _now, _raw: None, lambda: 0)
    player = Player(desktop.send, time.sleep)
    steps = item.get("steps") or events_to_steps(item["events"])
    events = steps_to_events(steps) if steps else item["events"]
    queue = NumberQueue.from_dict(item.get("numbers"))
    spans = digit_spans(events)
    before_grid: list = [None]

    def prepare(_loop: int) -> list:
        before_grid[0] = capture_grid()
        if queue.range_mode:
            value = queue.take()
            if not value:
                return []
            print(f"Số: {value}")
            _script, batch = compile_script(number_enter_steps(value))
            return batch
        batch, value = apply_turn(events, spans, queue)
        if value:
            print(f"Số: {value}")
        return batch

    def after(_loop: int) -> list:
        if not queue.range_mode or not queue.last_used:
            return []
        time.sleep(0.5)
        changed, percent, clicks = recover_after_enter(
            before_grid[0],
            capture_grid(),
            queue.change_threshold,
            queue.recover_clicks,
        )
        if not changed:
            return []
        queue.flag(queue.last_used)
        print(f"Đổi màn {percent:.0f}% — lưu {queue.last_used}")
        if len(clicks) < RECOVER_CLICKS:
            return []
        sw, sh = current_screen_size()
        mapped = scale_clicks(clicks, sw, sh) if sw and sh else list(clicks)
        play_named_clicks(mapped, desktop.send, time.sleep)
        return []

    print(f"Đang làm lại «{item['name']}»…")
    result = player.play(events, times=args.repeat, prepare=prepare, after=after)
    if item.get("slug"):
        store.save(
            item["name"],
            events,
            slug=item["slug"],
            numbers=queue.to_dict(),
            steps=steps,
        )
    if result["stopped"]:
        print(f"Đã dừng sau {result['loops']} lần.")
    else:
        print(f"Đã làm xong {result['loops']} lần.")
    return 0


def _sync(store: MacroStore, action: str, name: str | None) -> int:
    try:
        if action == "pull":
            result = pull(store, name)
            verb = "Đã lấy"
        elif action == "push":
            result = push(store, name)
            verb = "Đã đưa lên"
        else:
            print(f"Lệnh kho không rõ: {action}")
            return 1
    except HubError as exc:
        print(str(exc))
        return 1
    except ValueError as exc:
        print(str(exc))
        return 1
    print(f"{verb} {result['count']} kịch bản từ {describe()}.")
    for item in result.get("items") or []:
        print(f"{item.get('slug')}\t{item.get('name')}")
    return 0


def _run_update(*, then_gui: bool) -> int:
    result = apply_if_needed(relaunch=False)
    if result.get("error"):
        print(result["error"])
        return 1 if not then_gui else _launch_gui(skip_update=True)
    if result.get("skipped"):
        print("Bỏ qua cập nhật (máy dev hoặc PC_MACRO_SKIP_UPDATE).")
    elif result.get("updated"):
        applied = result.get("applied") or {}
        print(f"Đã tự cập nhật lên bản {applied.get('build') or '?'}.")
    else:
        current = result.get("current") or {}
        print(f"Đã là bản mới nhất ({current.get('build') or '?'}).")
    if then_gui:
        return _launch_gui(skip_update=True)
    return 0


def _launch_gui(*, skip_update: bool = False) -> int:
    # Tk is optional. Keep this import here so `list` / `show` run on a server
    # that has no tkinter, instead of failing at module import.
    if not skip_update:
        result = apply_if_needed(relaunch=True)
        if result.get("relaunch"):
            return 0
    from pc_macro.gui import main as gui_main

    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
