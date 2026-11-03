"""Клавиша на клавиатуре экспериментатора, в каком бы окне ни был фокус (X11).

Игра на весь экран забирает клавиатуру, поэтому читать терминал нельзя. XGrabKey на
корневом окне отдаёт нажатие только нам: игра его не видит (у AirSim R — запись видео,
она и не включится). Захват по коду клавиши, поэтому R работает и в русской раскладке
(та же клавиша «К»). Через ctypes и libX11, без лишних пакетов.

  with GlobalKey("r") as key:
      ...
      if key.pressed(): ...
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import select
import threading
import time

ANY_MODIFIER = 1 << 15
GRAB_MODE_ASYNC = 1
KEY_PRESS = 2
#: Удержание клавиши шлёт автоповтор — повторные нажатия чаще этого не считаем.
DEBOUNCE_S = 0.5


class GlobalKey:
    def __init__(self, keysym: str = "r") -> None:
        self.keysym = keysym
        self.available = False
        self.error = ""
        self._count = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> GlobalKey:
        if not os.environ.get("DISPLAY"):
            self.error = "нет DISPLAY (не графический сеанс)"
            return self
        path = ctypes.util.find_library("X11")
        if not path:
            self.error = "нет libX11"
            return self
        x = ctypes.cdll.LoadLibrary(path)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int]
        x.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self._x = x
        # Ошибка X (клавиша уже захвачена кем-то) по умолчанию убивает процесс — перехватываем.
        handler_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        self._failed = False

        def on_error(_display, _event):
            self._failed = True
            return 0

        self._handler = handler_type(on_error)
        x.XSetErrorHandler(self._handler)
        self._dpy = x.XOpenDisplay(None)
        if not self._dpy:
            self.error = "не открыть дисплей X"
            return self
        self._root = x.XDefaultRootWindow(self._dpy)
        self._keycode = x.XKeysymToKeycode(self._dpy, x.XStringToKeysym(self.keysym.encode()))
        x.XGrabKey(self._dpy, self._keycode, ANY_MODIFIER, self._root, 1,
                   GRAB_MODE_ASYNC, GRAB_MODE_ASYNC)
        x.XSync(self._dpy, 0)
        if self._failed:
            self.error = f"клавишу {self.keysym.upper()} уже захватила другая программа"
            x.XCloseDisplay(self._dpy)
            self._dpy = None
            return self
        self.available = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        x, dpy = self._x, self._dpy
        fd = x.XConnectionNumber(dpy)
        event = ctypes.create_string_buffer(256)  # XEvent — объединение размером 192 байта
        last = 0.0
        while not self._stop.is_set():
            if not x.XPending(dpy):
                select.select([fd], [], [], 0.1)
                continue
            x.XNextEvent(dpy, event)
            if ctypes.c_int.from_buffer(event).value == KEY_PRESS:
                now = time.monotonic()
                if now - last >= DEBOUNCE_S:
                    with self._lock:
                        self._count += 1
                last = now

    def pressed(self) -> int:
        """Сколько раз нажали с прошлого вызова."""
        with self._lock:
            n, self._count = self._count, 0
        return n

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if getattr(self, "_dpy", None):
            self._x.XUngrabKey(self._dpy, self._keycode, ANY_MODIFIER, self._root)
            self._x.XSync(self._dpy, 0)
            self._x.XCloseDisplay(self._dpy)
            self._dpy = None
