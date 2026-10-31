"""Алгоритмы управления и их загрузка по имени.

Имя алгоритма в сценарии может быть:
  * коротким именем встроенного алгоритма: `single_file`, `leader_path`, `line_follow`, ...;
  * `пакет.модуль:Класс` — класс из установленного Python-модуля;
  * `путь/к/файлу.py:Класс` — класс из отдельного файла.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from .base import FollowerAlgorithm
from .leader_path import LeaderPath
from .line_follow import LineFollow
from .single_file import SingleFile
from .stop_and_go import StopAndGoPilot
from .vision import Flock, VisualFollow
from .waypoints import WaypointPilot

BUILTIN: dict[str, type[FollowerAlgorithm]] = {
    "single_file": SingleFile,
    "leader_path": LeaderPath,
    "line_follow": LineFollow,
    "waypoints": WaypointPilot,
    "stop_and_go": StopAndGoPilot,
    "visual_follow": VisualFollow,
    "flock": Flock,
}


def _gamepad() -> type[FollowerAlgorithm]:
    from .gamepad import GamepadPilot  # pygame нужен только здесь

    return GamepadPilot


def load_algorithm(name: str) -> type[FollowerAlgorithm]:
    if name in BUILTIN:
        return BUILTIN[name]
    if name == "gamepad":
        return _gamepad()
    if ":" not in name:
        known = ", ".join([*BUILTIN, "gamepad"])
        raise ValueError(f"Неизвестный алгоритм '{name}'. Встроенные: {known}")

    target, class_name = name.rsplit(":", 1)
    if target.endswith(".py"):
        path = Path(target)
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Не удалось загрузить файл {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)

    cls = getattr(module, class_name)
    if not (isinstance(cls, type) and issubclass(cls, FollowerAlgorithm)):
        raise TypeError(f"{name} не наследует FollowerAlgorithm")
    return cls


__all__ = ["BUILTIN", "Flock", "FollowerAlgorithm", "LeaderPath", "SingleFile", "VisualFollow",
           "WaypointPilot", "load_algorithm"]
