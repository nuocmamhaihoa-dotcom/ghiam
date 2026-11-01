"""Launch the recorder window, or play a saved macro from the terminal."""

from __future__ import annotations

import argparse
import sys
import time
from typing import Sequence

from pc_macro.desktop import Desktop, available, install_hint
from pc_macro.events import default_title, script_lines
from pc_macro.numbers import NumberQueue, apply_turn, digit_spans
from pc_macro.session import Player
from pc_macro.store import MacroStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pc-macro",
        description="Ghi nhớ thao tác bàn phím và chuột trên máy, rồi làm lại.",
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

    args = parser.parse_args(argv)
    if args.cmd in (None, "gui"):
        return _launch_gui()
    store = MacroStore()
    if args.cmd == "list":
        items = store.list()
        if not items:
            print("Chưa có bản ghi. Chạy: python -m pc_macro")
            return 0
        for item in items:
            print(f"{item['slug']}\t{item['name']}\t{item['event_count']} thao tác")
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
        print(item["name"] or default_title(item["events"]))
        for line in script_lines(item["events"]):
            print(line)
        return 0
    if not available():
        print(install_hint())
        return 1
    if args.delay > 0:
        print(f"Bắt đầu sau {args.delay:.0f} giây…")
        time.sleep(args.delay)
    desktop = Desktop(lambda _now, _raw: None, lambda: 0)
    player = Player(desktop.send, time.sleep)
    queue = NumberQueue.from_dict(item.get("numbers"))
    spans = digit_spans(item["events"])

    def prepare(_loop: int) -> list:
        batch, value = apply_turn(item["events"], spans, queue)
        if value:
            print(f"Số: {value}")
        return batch

    print(f"Đang làm lại «{item['name']}»…")
    result = player.play(item["events"], times=args.repeat, prepare=prepare)
    if item.get("slug"):
        store.save(item["name"], item["events"], slug=item["slug"], numbers=queue.to_dict())
    if result["stopped"]:
        print(f"Đã dừng sau {result['loops']} lần.")
    else:
        print(f"Đã làm xong {result['loops']} lần.")
    return 0


def _launch_gui() -> int:
    # Tk is optional. Keep this import here so `list` / `show` run on a server
    # that has no tkinter, instead of failing at module import.
    from pc_macro.gui import main as gui_main

    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
