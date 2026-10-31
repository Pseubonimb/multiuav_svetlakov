"""Нажатия клавиш в терминале во время полёта — без Enter.

Работает, только если run запущен прямо в терминале (как в scripts/demo.sh).
Ctrl+C по-прежнему останавливает полёт.
"""

from __future__ import annotations

import os
import queue
import select
import sys
import threading


class Keyboard:
    """with Keyboard() as keys: ... keys.pressed() — клавиши, нажатые с прошлого вызова."""

    def __init__(self) -> None:
        self._keys: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._saved = None

    def __enter__(self) -> Keyboard:
        if not sys.stdin.isatty():
            return self  # журнал в файл, тесты: клавиш нет
        import termios
        import tty

        fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)  # символы приходят сразу, Ctrl+C работает
        threading.Thread(target=self._read, args=(fd,), daemon=True).start()
        return self

    def _read(self, fd: int) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                data = os.read(fd, 8).decode("utf-8", errors="ignore")
                for char in data:
                    self._keys.put(char)

    def pressed(self) -> list[str]:
        keys = []
        while not self._keys.empty():
            keys.append(self._keys.get_nowait())
        return keys

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._saved is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved)
