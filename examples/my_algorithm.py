"""Пример своего алгоритма. Запуск:

    python -m multiuav run scenarios/corner.yaml --algorithm examples/my_algorithm.py:Cautious

Алгоритм — это класс с методом step(). На входе свой дрон, остальные дроны
и ведущий (позиции и скорости в метрах, система NED), на выходе — RcOverride.
"""

import math

import numpy as np

from multiuav.algorithms.base import FollowerAlgorithm
from multiuav.types import RcOverride, wrap_angle


class Cautious(FollowerAlgorithm):
    """«Гуськом», но притормаживает, когда предыдущий дрон резко поворачивает."""

    def step(self, me, others, leader, t):
        pred = self.predecessor(others, leader)
        delta = pred.position[:2] - me.position[:2]
        dist = float(np.linalg.norm(delta))
        if dist < 1e-3:
            return RcOverride()  # все стики в центре: висим на месте

        direction = delta / dist
        pred_course = math.atan2(pred.velocity[1], pred.velocity[0])
        turn = abs(wrap_angle(pred_course - me.yaw)) if pred.speed > 1 else 0.0
        slow_down = 1.0 if turn < math.radians(20) else 0.5
        speed = float(np.clip(slow_down * (pred.speed + 0.8 * (dist - 6.0)), 0.0, 8.0))

        # Помощник self.velocity переводит «скорость, курс, высота» в стики.
        # Можно и напрямую: RcOverride(roll=1500, pitch=1700, throttle=1500, yaw=1500).
        yaw = math.atan2(direction[1], direction[0])
        return self.velocity.command(me, direction * speed, yaw, pred.altitude)
