"""Запустить Unreal с Cosys-AirSim, дождаться его и закрыть: общее для `sim` и будущего меню.

Unreal закрывается только по PID своего процесса: `pkill -f …Blocks.uproject` находит и
оболочку, в командной строке которой есть тот же путь, и убивает её вместе с собой.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time

#: Порт RPC-сервера Cosys-AirSim.
PORT = 41451


def status(text: str) -> None:
    """Строка «STATUS ЧЧ:ММ:СС текст» — её читает меню, человеку она тоже понятна."""
    print(f"STATUS {time.strftime('%H:%M:%S')} {text}", flush=True)


def port_open(port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop_process(proc: subprocess.Popen | None, name: str, sig=signal.SIGTERM,
                 wait_s: float = 10.0) -> None:
    """Закрыть по PID: сначала вежливо (sig), через wait_s — kill -9."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(sig)
        proc.wait(timeout=wait_s)
    except subprocess.TimeoutExpired:
        status(f"{name} не закрылся за {wait_s:.0f} с — kill -9")
        proc.kill()
        proc.wait(timeout=5)


def game_command(engine: str, project: str, map_path: str, quality: int = 1,
                 fullscreen: bool = False) -> list[str]:
    """Unreal в режиме игры (-game): карта открывается сразу, Play нажимать не нужно."""
    cmd = [os.path.expanduser(engine), os.path.expanduser(project), map_path, "-game", "-nosplash",
           # DisableAllScreenMessages — без красного предупреждения Lumen в углу экрана.
           f"-ExecCmds=scalability {quality}, DisableAllScreenMessages"]
    if fullscreen:
        cmd += ["-fullscreen"]
    else:
        cmd += ["-windowed", "-ResX=1600", "-ResY=900", "-WinX=0", "-WinY=0"]
    return cmd


def editor_command(engine: str, project: str, map_path: str) -> list[str]:
    """Редактор на нужной карте; Play нажимает человек."""
    return [os.path.expanduser(engine), os.path.expanduser(project), map_path]


def game_env() -> dict:
    env = dict(os.environ)
    # Те же переменные, что в alias ue5: рисует дискретная NVIDIA (гибридная графика).
    env.update(__NV_PRIME_RENDER_OFFLOAD="1", __GLX_VENDOR_LIBRARY_NAME="nvidia")
    return env


def wait_game(game: subprocess.Popen | None, timeout_s: int, stop: threading.Event) -> None:
    """Ждать, пока Cosys откроет порт; Unreal упал или время вышло — RuntimeError."""
    status(f"жду Cosys (порт {PORT}, до {timeout_s} с)")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not stop.is_set():
        if game is not None and game.poll() is not None:
            raise RuntimeError(f"Unreal закрылся при запуске (код {game.returncode}), см. unreal.log")
        if port_open():
            time.sleep(2.0)  # порт открылся раньше, чем дрон появился в мире
            return
        time.sleep(1.0)
    raise RuntimeError(f"Cosys не ответил за {timeout_s} с, см. unreal.log")
