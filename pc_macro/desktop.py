"""Talk to the real keyboard and mouse through pynput, when it is installed."""

from __future__ import annotations

from typing import Any, Callable

try:
    from pynput import keyboard as pynput_keyboard
    from pynput import mouse as pynput_mouse
except ImportError:
    pynput_keyboard = None
    pynput_mouse = None

ListenFn = Callable[[int, dict[str, Any]], None]

_KEY_ALIASES = {
    "escape": "esc",
    "esc": "esc",
    "return": "enter",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt_gr": "alt",
    "shift_l": "shift",
    "shift_r": "shift",
    "cmd": "cmd",
    "cmd_l": "cmd",
    "cmd_r": "cmd",
}

_SPECIAL = (
    "enter",
    "tab",
    "space",
    "backspace",
    "delete",
    "esc",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "page_up",
    "page_down",
    "shift",
    "ctrl",
    "alt",
    "cmd",
    "caps_lock",
    "insert",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
)


def available() -> bool:
    return pynput_keyboard is not None and pynput_mouse is not None


def install_hint() -> str:
    return "Cần cài pynput trên máy này: pip install pynput"


class Desktop:
    """Listen for real input and send recorded events back to the OS."""

    def __init__(self, on_event: ListenFn, now_ms: Callable[[], int]) -> None:
        if not available():
            raise RuntimeError(install_hint())
        self._on_event = on_event
        self._now_ms = now_ms
        self._keyboard = pynput_keyboard.Controller()
        self._mouse = pynput_mouse.Controller()
        self._key_map = _build_key_map()
        self._button_map = {
            "left": pynput_mouse.Button.left,
            "right": pynput_mouse.Button.right,
            "middle": pynput_mouse.Button.middle,
        }
        self._listeners: list[Any] = []

    def start(self) -> None:
        key_listener = pynput_keyboard.Listener(on_press=self._press, on_release=self._release)
        mouse_listener = pynput_mouse.Listener(on_click=self._click, on_scroll=self._scroll)
        self._listeners = [key_listener, mouse_listener]
        for listener in self._listeners:
            listener.start()

    def stop(self) -> None:
        for listener in self._listeners:
            listener.stop()
        self._listeners = []

    def send(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if kind == "key":
            key = self._to_key(str(event.get("key") or ""))
            if key is None:
                return
            if event.get("action") == "down":
                self._keyboard.press(key)
            else:
                self._keyboard.release(key)
            return
        if kind == "click":
            self._mouse.position = (int(event["x"]), int(event["y"]))
            button = self._button_map.get(str(event.get("button") or "left"))
            if button is None:
                return
            if event.get("action") == "down":
                self._mouse.press(button)
            else:
                self._mouse.release(button)
            return
        if kind == "scroll":
            self._mouse.position = (int(event["x"]), int(event["y"]))
            self._mouse.scroll(int(event.get("dx") or 0), int(event.get("dy") or 0))

    def _press(self, key: Any) -> None:
        name = key_name(key)
        if name is None:
            return
        self._on_event(self._now_ms(), {"kind": "key", "action": "down", "key": name})

    def _release(self, key: Any) -> None:
        name = key_name(key)
        if name is None:
            return
        self._on_event(self._now_ms(), {"kind": "key", "action": "up", "key": name})

    def _click(self, x: float, y: float, button: Any, pressed: bool) -> None:
        name = getattr(button, "name", "left")
        self._on_event(
            self._now_ms(),
            {
                "kind": "click",
                "action": "down" if pressed else "up",
                "button": str(name),
                "x": x,
                "y": y,
            },
        )

    def _scroll(self, x: float, y: float, dx: float, dy: float) -> None:
        self._on_event(
            self._now_ms(),
            {"kind": "scroll", "x": x, "y": y, "dx": int(dx), "dy": int(dy)},
        )

    def _to_key(self, name: str) -> Any:
        if len(name) == 1:
            return name
        return self._key_map.get(name)


def key_name(key: Any) -> str | None:
    char = getattr(key, "char", None)
    if isinstance(char, str) and len(char) == 1 and char.isprintable() and not char.isspace():
        return char
    name = getattr(key, "name", None)
    if isinstance(name, str) and name:
        lowered = name.lower()
        return _KEY_ALIASES.get(lowered, lowered)
    if isinstance(char, str) and char == " ":
        return "space"
    return None


def _build_key_map() -> dict[str, Any]:
    if pynput_keyboard is None:
        return {}
    mapped: dict[str, Any] = {}
    for name in _SPECIAL:
        value = getattr(pynput_keyboard.Key, name, None)
        if value is not None:
            mapped[name] = value
    return mapped
