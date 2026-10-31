"""Автопилот ведущего «аккуратный оператор»: для повторяемых прогонов вместо человека."""

from __future__ import annotations

import math

import numpy as np

from ..types import DroneState, RcOverride
from .base import FollowerAlgorithm


class StopAndGoPilot(FollowerAlgorithm):
    """Летит по точкам, плавно разгоняясь и тормозя; в точке с четвёртым числом
    зависает на столько секунд (как пилот, который остановился осмотреться).

    Параметры: waypoints — [север, восток, высота] или [север, восток, высота, пауза_с];
    speed — м/с (5); accel — м/с² (2); accept_radius — м (1.5).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.wps = [list(map(float, w)) for w in self.param("waypoints", [])]
        self.i, self.wait_until, self.v_cmd = 0, None, 0.0
        self.t_prev: float | None = None

    def step(self, me: DroneState, others: list[DroneState], leader: DroneState | None,
             t: float) -> RcOverride:
        if not self.wps:
            return self.velocity.command(me, np.zeros(2), None, me.altitude)
        if self.i >= len(self.wps):   # маршрут пройден: зависаем
            return self.velocity.command(me, np.zeros(2), None, self.wps[-1][2])
        wp = self.wps[self.i]
        delta = np.array(wp[:2]) - me.position[:2]
        dist = float(np.linalg.norm(delta))
        stop_here = len(wp) > 3 or self.i == len(self.wps) - 1
        if self.wait_until is not None:
            if t < self.wait_until:
                return self.velocity.command(me, np.zeros(2), None, wp[2])
            self.wait_until = None
            self.i += 1
            return self.step(me, others, leader, t)
        if dist < float(self.param("accept_radius", 1.5)):
            if stop_here:
                self.wait_until = t + (wp[3] if len(wp) > 3 else math.inf)
                self.v_cmd = 0.0
                return self.velocity.command(me, np.zeros(2), None, wp[2])
            self.i += 1
            return self.step(me, others, leader, t)
        acc, vmax = float(self.param("accel", 2.0)), float(self.param("speed", 5.0))
        dt = 0.05 if self.t_prev is None else min(0.5, t - self.t_prev)
        self.t_prev = t
        v_lim = math.sqrt(2 * acc * max(dist - 1.0, 0.0)) if stop_here else vmax
        self.v_cmd = min(vmax, v_lim, self.v_cmd + acc * dt)
        u = delta / dist
        return self.velocity.command(me, u * self.v_cmd, math.atan2(u[1], u[0]), wp[2])
