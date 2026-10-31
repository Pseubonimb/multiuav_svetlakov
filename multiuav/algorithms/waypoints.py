"""Автопилот ведущего по точкам: заменяет оператора в повторяемых прогонах и тестах."""

from __future__ import annotations

import math

import numpy as np

from ..types import DroneState, RcOverride
from .base import FollowerAlgorithm


class WaypointPilot(FollowerAlgorithm):
    """Летит по списку точек с постоянной скоростью.

    Параметры: waypoints — список [север, восток, высота] в метрах;
    speed — м/с (5); accept_radius — когда точка считается пройденной, м (2).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.waypoints = [np.asarray(w, dtype=float) for w in self.param("waypoints", [])]
        self.index = 0

    @property
    def done(self) -> bool:
        return self.index >= len(self.waypoints)

    def step(self, me: DroneState, others: list[DroneState], leader: DroneState,
             t: float) -> RcOverride:
        if not self.waypoints:
            return self.velocity.command(me, np.zeros(2), None, me.altitude)
        if self.done:  # маршрут пройден: зависаем
            return self.velocity.command(me, np.zeros(2), None, float(self.waypoints[-1][2]))
        speed = float(self.param("speed", 5.0))
        accept = float(self.param("accept_radius", 2.0))

        while self.index < len(self.waypoints) - 1:
            if np.linalg.norm(self.waypoints[self.index][:2] - me.position[:2]) > accept:
                break
            self.index += 1
        wp = self.waypoints[self.index]
        delta = wp[:2] - me.position[:2]
        dist = float(np.linalg.norm(delta))
        if self.index == len(self.waypoints) - 1:
            speed = min(speed, 0.8 * dist)  # плавно тормозим у последней точки
            if dist < accept:
                self.index = len(self.waypoints)
        if dist < 1e-3:
            return self.velocity.command(me, np.zeros(2), None, float(wp[2]))
        yaw = math.atan2(delta[1], delta[0]) if dist > accept else None
        return self.velocity.command(me, delta / dist * speed, yaw, float(wp[2]))
