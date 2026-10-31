"""«По следу ведущего»: дрон повторяет траекторию ведущего, а не летит на соседа напрямую."""

from __future__ import annotations

import math

import numpy as np

from ..types import DroneState, RcOverride
from .base import FollowerAlgorithm


class LeaderPath(FollowerAlgorithm):
    """Запоминает, где пролетал ведущий, и держится на этом следе, отставая
    от него на `distance` × (номер дрона) метров пути. На повороте проходит
    там же, где прошёл ведущий, и не срезает угол. Ошибки не накапливаются
    вдоль колонны: каждый идёт по следу ведущего, а не соседа.

    Параметры: distance — интервал между дронами по длине пути, м (6);
    gain — как резко возвращаться на след, 1/с (1.5); lookahead — на сколько
    метров вперёд по следу целиться (2); max_speed — м/с (10);
    spacing — шаг записи следа, м (0.5).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.trail: list[np.ndarray] = []  # точки (север, восток, высота)

    def step(self, me: DroneState, others: list[DroneState], leader: DroneState,
             t: float) -> RcOverride:
        distance = float(self.param("distance", 6.0)) * self.drone_id
        gain = float(self.param("gain", 1.5))
        lookahead = float(self.param("lookahead", 2.0))
        max_speed = float(self.param("max_speed", 10.0))
        spacing = float(self.param("spacing", 0.5))

        head = np.array([leader.position[0], leader.position[1], leader.altitude])
        if not self.trail:
            # Что было до старта, неизвестно: считаем, что ведущий пришёл по прямой от нас.
            self.trail = [np.array([me.position[0], me.position[1], me.altitude])]
        if np.linalg.norm(head[:2] - self.trail[-1][:2]) >= spacing:
            self.trail.append(head)

        slot, _, index = self._point_behind(head, distance)
        aim, tangent, _ = self._point_behind(head, max(0.0, distance - lookahead))
        del self.trail[:max(0, index - 1)]

        # Насколько мы отстали от своего места на следе (по направлению следа).
        lag = float((slot[:2] - me.position[:2]) @ tangent)
        to_aim = aim[:2] - me.position[:2]
        dist_aim = float(np.linalg.norm(to_aim))
        speed = float(np.clip(leader.speed + gain * lag, 0.0, max_speed))
        if dist_aim > 1e-3:
            velocity = to_aim / dist_aim * speed
        else:
            velocity = tangent * speed
        yaw = math.atan2(velocity[1], velocity[0]) if speed > 0.5 else None
        return self.velocity.command(me, velocity, yaw, float(slot[2]))

    def _point_behind(self, head: np.ndarray, distance: float
                      ) -> tuple[np.ndarray, np.ndarray, int]:
        """Точка следа на `distance` м пути позади `head`, направление следа в ней
        и индекс начала её отрезка в self.trail."""
        points = [*self.trail, head]
        remaining = distance
        for i in range(len(points) - 1, 0, -1):
            a, b = points[i - 1], points[i]
            seg = b[:2] - a[:2]
            length = float(np.linalg.norm(seg))
            if length < 1e-6:
                continue
            tangent = seg / length
            if length >= remaining:
                return b + (a - b) * (remaining / length), tangent, i - 1
            remaining -= length
        # След короче нужного: продлеваем его назад по первому отрезку.
        first = points[0]
        tangent = np.zeros(2)
        for nxt in points[1:]:
            seg = nxt[:2] - first[:2]
            if np.linalg.norm(seg) > 1e-6:
                tangent = seg / np.linalg.norm(seg)
                break
        target = first.copy()
        target[:2] -= tangent * remaining
        return target, tangent, 0
