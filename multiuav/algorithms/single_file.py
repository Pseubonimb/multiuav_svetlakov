"""«Гуськом»: каждый дрон летит прямо на предыдущий и смотрит ему в затылок."""

from __future__ import annotations

import math

import numpy as np

from ..types import DroneState, RcOverride
from .base import FollowerAlgorithm


class SingleFile(FollowerAlgorithm):
    """Простейшее следование. На повороте каждый следующий дрон срезает угол
    сильнее предыдущего, поэтому хвост колонны может задеть здание.

    Параметры: distance — дистанция до предыдущего, м (6);
    gain — как резко догонять, 1/с (0.8); max_speed — м/с (10).
    """

    def step(self, me: DroneState, others: list[DroneState], leader: DroneState,
             t: float) -> RcOverride:
        pred = self.predecessor(others, leader)
        distance = float(self.param("distance", 6.0))
        gain = float(self.param("gain", 0.8))
        max_speed = float(self.param("max_speed", 10.0))

        delta = pred.position[:2] - me.position[:2]
        dist = float(np.linalg.norm(delta))
        if dist < 1e-3:
            return self.velocity.command(me, np.zeros(2), None, pred.altitude)
        direction = delta / dist
        closing = max(0.0, float(pred.velocity[:2] @ direction))
        speed = float(np.clip(closing + gain * (dist - distance), 0.0, max_speed))
        yaw = math.atan2(direction[1], direction[0])
        return self.velocity.command(me, direction * speed, yaw, pred.altitude)
