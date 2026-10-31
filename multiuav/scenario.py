"""Сценарий эксперимента: сколько дронов, кто чем управляется, где здания и финиш."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .control import ControlLimits


@dataclass(frozen=True)
class AlgorithmSpec:
    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Building:
    """Здание-параллелепипед: границы в плане (север, восток) и высота, м."""

    north: tuple[float, float]
    east: tuple[float, float]
    height: float


@dataclass(frozen=True)
class Finish:
    """Финишная зона: прямоугольник в плане (север, восток), м."""

    north: tuple[float, float]
    east: tuple[float, float]

    def reached(self, position: np.ndarray) -> bool:
        return (self.north[0] <= position[0] <= self.north[1]
                and self.east[0] <= position[1] <= self.east[1])


@dataclass(frozen=True)
class Perception:
    """Что дрон видит сам, без связи с другими дронами.

    lidar_range — дальность кругового дальномера (лидара), м; 0 — без дальномера.
    sectors — на сколько секторов делится круг дальномера.
    vision_range — с какого расстояния камеры замечают другой дрон, м.
    fov_deg — поле зрения камер вокруг курса дрона, градусы (360 — круговой обзор).
    noise — ошибка оценки положения соседа, доля от расстояния до него (0.02 — 2 %).
    """

    lidar_range: float = 20.0
    sectors: int = 72
    vision_range: float = 40.0
    fov_deg: float = 360.0
    noise: float = 0.02


def _duration(raw: Any) -> float | None:
    """`duration_s: null` или 0 в YAML — летать без ограничения времени."""
    if raw is None or float(raw) <= 0:
        return None
    return float(raw)


def _rate_gains(raw: Any) -> tuple[float, float, float] | None:
    """`rate_gains: {p: 0.5, i: 0.1, d: 0}` в YAML; пропущенные i и d — ноль."""
    if not raw:
        return None
    return float(raw["p"]), float(raw.get("i", 0.0)), float(raw.get("d", 0.0))


@dataclass
class Scenario:
    name: str
    drones: int
    leader: AlgorithmSpec
    followers: AlgorithmSpec
    rate_hz: float = 20.0
    duration_s: float | None = 120.0  # None — без ограничения, до Ctrl+C
    start_north: float = 0.0
    start_east: float = 0.0
    altitude: float = 10.0
    heading: float = 0.0  # рад
    spacing: float = 6.0  # м между дронами на старте
    buildings: list[Building] = field(default_factory=list)
    finish: Finish | None = None
    limits: ControlLimits = ControlLimits()
    show_rc: bool = False  # показывать каналы RC дрона 0 на экране симулятора
    # True — разбитый дрон выбывает, прогон кончается, когда выбыли все.
    # False — для ручных полётов: столкновение записывается, но управление продолжается.
    stop_on_collision: bool = True
    perception: Perception = Perception()
    # Модель дрона в Cosys-AirSim: generic (обычный, тяга/вес ~1,7) или fast<k>, например fast2.5
    # (тяга x k, наклон до 45°, вращение до 5 рад/с; fast без числа — x1.3; нужна доработка плагина).
    model: str = "generic"
    # Коэффициенты (P, I, D) регулятора угловых скоростей SimpleFlight, одни на крен, тангаж
    # и рысканье. None — как в плагине (generic: P 0.25; fast<k>: 0.25/k).
    rate_gains: tuple[float, float, float] | None = None
    # Сопротивление винтов в Cosys, с/м: сила -k * тяга * скорость поперёк оси винтов. Без него
    # (0, как в исходном AirSim) дрон скользит, как по льду. Нужен патч плагина 04. В kinematic
    # похожее сопротивление есть всегда (drag 0.35 1/с ≈ k 0.036 на висении).
    rotor_drag: float = 0.0

    def start_positions(self) -> list[np.ndarray]:
        """Стартовые точки NED: ведущий впереди, остальные колонной позади него."""
        back = -np.array([math.cos(self.heading), math.sin(self.heading)])
        points = []
        for i in range(self.drones):
            ne = np.array([self.start_north, self.start_east]) + back * self.spacing * i
            points.append(np.array([ne[0], ne[1], -self.altitude]))
        return points

    @classmethod
    def load(cls, path: str | Path) -> Scenario:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data, default_name=Path(path).stem)

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_name: str = "scenario") -> Scenario:
        start = data.get("start", {})
        world = data.get("world", {})
        limits = data.get("limits", {})
        finish = data.get("finish")
        perception = data.get("perception", {})

        def spec(key: str, default: str) -> AlgorithmSpec:
            raw = data.get(key, {})
            if isinstance(raw, str):
                return AlgorithmSpec(raw)
            return AlgorithmSpec(raw.get("algorithm", default), dict(raw.get("params") or {}))

        return cls(
            name=data.get("name", default_name),
            drones=int(data.get("drones", 5)),
            leader=spec("leader", "gamepad"),
            followers=spec("followers", "single_file"),
            rate_hz=float(data.get("rate_hz", 20.0)),
            duration_s=_duration(data.get("duration_s", 120.0)),
            start_north=float(start.get("north", 0.0)),
            start_east=float(start.get("east", 0.0)),
            altitude=float(start.get("altitude", 10.0)),
            heading=math.radians(float(start.get("heading_deg", 0.0))),
            spacing=float(start.get("spacing", 6.0)),
            buildings=[Building(tuple(b["north"]), tuple(b["east"]), float(b.get("height", 50.0)))
                       for b in world.get("buildings", [])],
            finish=Finish(tuple(finish["north"]), tuple(finish["east"])) if finish else None,
            limits=ControlLimits(
                max_tilt=math.radians(float(limits.get("max_tilt_deg", 30.0))),
                max_yaw_rate=math.radians(float(limits.get("max_yaw_rate_deg", 90.0))),
                max_climb=float(limits.get("max_climb", 3.0)),
                max_rate=math.radians(float(limits.get("max_rate_deg", 200.0))),
                hover_throttle=float(limits.get("hover_throttle", 0.5)),
            ),
            show_rc=bool(data.get("show_rc", False)),
            stop_on_collision=bool(data.get("stop_on_collision", True)),
            perception=Perception(**{k: type(getattr(Perception(), k))(v)
                                     for k, v in perception.items()}),
            model=str(data.get("model", "generic")).lower(),
            rate_gains=_rate_gains(data.get("rate_gains")),
            rotor_drag=float(data.get("rotor_drag", 0.0)),
        )
