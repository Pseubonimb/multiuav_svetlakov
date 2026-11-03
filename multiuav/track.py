"""Геометрия трассы: ворота и зоны затемнения из maps/<карта>.json.

Файл пишет скрипт tools/unreal/friday_track.py (Python в редакторе Unreal).
Оси — как у MultiUAV: north, east в метрах от точки старта, alt — вверх.
Порядок ворот и зон в файле — по ходу трассы.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Пересечение плоскости ворот дальше этого от центра — не попытка пройти ворота,
#: а пролёт где-то ещё по коридору (у соседнего препятствия, на другом этаже).
ATTEMPT_RADIUS_M = 12.0


def _vec(p: dict) -> np.ndarray:
    return np.array([p["north"], p["east"], p["alt"]], dtype=float)


@dataclass(frozen=True)
class Gate:
    index: int
    name: str
    center: np.ndarray  # north, east, alt
    heading: float  # рад, курс полёта через ворота
    width: float
    height: float

    @property
    def forward(self) -> np.ndarray:
        return np.array([math.cos(self.heading), math.sin(self.heading), 0.0])

    def offset(self, point: np.ndarray) -> tuple[float, float]:
        """Смещение точки в плоскости ворот от центра: (вправо, вверх), м."""
        d = np.asarray(point, dtype=float) - self.center
        right = -d[0] * math.sin(self.heading) + d[1] * math.cos(self.heading)
        return float(right), float(d[2])

    def inside(self, point: np.ndarray) -> bool:
        right, up = self.offset(point)
        return abs(right) <= self.width / 2 and abs(up) <= self.height / 2

    def crossing(self, p0: np.ndarray, p1: np.ndarray) -> float | None:
        """Доля отрезка p0→p1, на которой он пересекает плоскость ворот по ходу трассы.

        None — не пересекает (или летит назад). Плоскость бесконечная: попадание
        в проём проверяется отдельно (inside), близость — по ATTEMPT_RADIUS_M.
        """
        f = self.forward
        s0 = float(np.dot(np.asarray(p0) - self.center, f))
        s1 = float(np.dot(np.asarray(p1) - self.center, f))
        if s0 < 0.0 <= s1 and s1 != s0:
            return -s0 / (s1 - s0)
        return None


@dataclass(frozen=True)
class Zone:
    index: int
    name: str
    label: str
    center: np.ndarray
    half: np.ndarray  # полуразмеры по north, east, up

    def contains(self, point: np.ndarray) -> bool:
        return bool(np.all(np.abs(np.asarray(point, dtype=float) - self.center) <= self.half))

    def entry(self, p0: np.ndarray, p1: np.ndarray) -> float | None:
        """Доля отрезка, на которой он входит в коробку зоны (снаружи → внутрь), иначе None."""
        if self.contains(p0) or not self.contains(p1):
            return None
        lo, hi = self.center - self.half, self.center + self.half
        d = np.asarray(p1, dtype=float) - np.asarray(p0, dtype=float)
        t_in, t_out = 0.0, 1.0
        for k in range(3):  # метод отсечения по граням
            if abs(d[k]) < 1e-12:
                continue
            a, b = (lo[k] - p0[k]) / d[k], (hi[k] - p0[k]) / d[k]
            t_in = max(t_in, min(a, b))
            t_out = min(t_out, max(a, b))
        return t_in if t_in <= t_out else 1.0


@dataclass(frozen=True)
class Track:
    map: str
    gates: list[Gate]
    zones: list[Zone]
    start_heading: float  # рад

    @classmethod
    def load(cls, path: str | Path) -> Track:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> Track:
        gates = [Gate(g["index"], g["name"], _vec(g["center"]), math.radians(g["heading_deg"]),
                      float(g["width_m"]), float(g["height_m"])) for g in data["gates"]]
        zones = [Zone(z["index"], z["name"], z.get("label", z["name"]), _vec(z["center"]),
                      np.array([z["half_size"]["north"], z["half_size"]["east"], z["half_size"]["up"]]))
                 for z in data["zones"]]
        return cls(data.get("map", ""), gates, zones,
                   math.radians(float(data.get("player_start_yaw_deg", 0.0))))

    def gate_passed(self, p0: np.ndarray, p1: np.ndarray) -> list[tuple[Gate, float]]:
        """Ворота, через проём которых прошёл отрезок p0→p1, и доля отрезка."""
        result = []
        for g in self.gates:
            a = g.crossing(p0, p1)
            if a is not None and g.inside(p0 + a * (p1 - p0)):
                result.append((g, a))
        return result


def expand_cyclic(values: list[float], count: int) -> list[float]:
    """Повторить значения по кругу до нужной длины: [75, 500] на 5 → 75, 500, 75, 500, 75."""
    if not values:
        return []
    return [values[i % len(values)] for i in range(count)]
