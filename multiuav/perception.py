"""Что дрон видит своими камерами — без радиосвязи с остальными.

Алгоритмы с `vision_only = True` получают не все дроны группы, а только тех соседей,
которых могли бы заметить сами: в пределах дальности и поля зрения камер, не
заслонённых зданием (проверка по своему дальномеру), и с ошибкой оценки положения,
растущей с расстоянием. Номер соседа считается различимым (например, по маркировке).
"""

from __future__ import annotations

import math

import numpy as np

from .scenario import Perception
from .types import DroneState, wrap_angle

#: Сосед считается заслонённым, если дальномер в его сторону упёрся в препятствие
#: ближе, чем до соседа, больше чем на столько метров.
OCCLUSION_MARGIN = 1.0
#: Ошибка оценки скорости соседа по видео, м/с.
VELOCITY_NOISE = 0.2


def sector_of(bearing: float, sectors: int) -> int:
    """Номер сектора дальномера для направления bearing (рад, 0 — север, по часовой)."""
    return int((bearing % (2 * math.pi)) / (2 * math.pi) * sectors) % sectors


def can_see(me: DroneState, other: DroneState, perception: Perception) -> bool:
    delta = other.position[:2] - me.position[:2]
    dist = float(np.hypot(*delta))
    if dist > perception.vision_range:
        return False
    bearing = math.atan2(delta[1], delta[0])
    if abs(wrap_angle(bearing - me.yaw)) > math.radians(perception.fov_deg) / 2:
        return False
    if me.ranges is not None:
        free = me.ranges[sector_of(bearing, len(me.ranges))]
        if free < dist - OCCLUSION_MARGIN:
            return False  # между нами здание
    return True


class Observer:
    """Глаза одного дрона: отбирает видимых соседей и добавляет ошибку оценки."""

    def __init__(self, drone_id: int, perception: Perception, seed: int = 0) -> None:
        self.perception = perception
        self.rng = np.random.default_rng(seed * 1000 + drone_id)

    def observe(self, me: DroneState, states: list[DroneState]
                ) -> tuple[list[DroneState], DroneState | None]:
        """Видимые соседи и ведущий (None, если его сейчас не видно)."""
        seen = []
        for other in states:
            if other.id == me.id or not can_see(me, other, self.perception):
                continue
            dist = float(np.linalg.norm(other.position[:2] - me.position[:2]))
            sigma = self.perception.noise * dist
            noisy = DroneState(
                other.id,
                other.position + self.rng.normal(0.0, sigma, 3) * np.array([1, 1, 0.3]),
                other.velocity + self.rng.normal(0.0, VELOCITY_NOISE, 3),
                other.yaw, other.collided)
            seen.append(noisy)
        leader = next((s for s in seen if s.id == 0), None)
        return seen, leader
