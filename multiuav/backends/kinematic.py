"""Упрощённый симулятор без графики: точечная модель квадрокоптера и здания-коробки.

Нужен, чтобы отлаживать алгоритмы и гонять серии прогонов на любой машине,
в том числе в автоматических тестах. Модель грубая: наклон -> ускорение,
линейное сопротивление воздуха, инерция наклона и вертикальной скорости.
Отлаженный здесь алгоритм потом проверяется в Cosys-AirSim.

Дальномер (лидар) моделируется лучами в горизонтальной плоскости: луч
упирается в здание, если дрон ниже его крыши. Другие дроны лучи не видят.
"""

from __future__ import annotations

import math

import numpy as np

from ..control import G, rc_to_acro, rc_to_setpoint
from ..scenario import Scenario
from ..types import ACRO, DroneState, RcOverride
from .base import SimBackend

MAX_ACRO_TILT = math.radians(80)
SOFT_LANDING = 1.5  # м/с вниз: медленнее — посадка, быстрее — удар о землю  # дальше модель не считаем: дрон всё равно падает


class KinematicBackend(SimBackend):
    realtime = False

    def __init__(self, drone_radius: float = 0.4, drag: float = 0.35,
                 tilt_tau: float = 0.15, climb_tau: float = 0.3) -> None:
        self.drone_radius = drone_radius
        self.drag = drag
        self.tilt_tau = tilt_tau
        self.climb_tau = climb_tau

    def start(self, scenario: Scenario) -> None:
        n = scenario.drones
        self.limits = scenario.limits
        self.buildings = scenario.buildings
        self.perception = scenario.perception
        self.pos = np.array(scenario.start_positions(), dtype=float)
        self.vel = np.zeros((n, 3))
        self.yaw = np.full(n, scenario.heading)
        self.tilt = np.zeros((n, 2))  # крен, тангаж
        self.collided = np.zeros(n, dtype=bool)
        self.commands = [RcOverride() for _ in range(n)]

    def states(self) -> list[DroneState]:
        return [DroneState(i, self.pos[i].copy(), self.vel[i].copy(), float(self.yaw[i]),
                           bool(self.collided[i]), self.ranges(i)) for i in range(len(self.pos))]

    def send(self, drone_id: int, rc: RcOverride) -> None:
        self.commands[drone_id] = rc

    def advance(self, dt: float) -> None:
        k_tilt = min(1.0, dt / self.tilt_tau)
        k_climb = min(1.0, dt / self.climb_tau)
        for i, rc in enumerate(self.commands):
            if self.collided[i]:
                continue
            if rc.mode == ACRO:
                self._advance_acro(i, rc, dt)
            else:
                sp = rc_to_setpoint(rc, self.limits)
                self.tilt[i] += (np.array([sp.roll, sp.pitch]) - self.tilt[i]) * k_tilt
                self.yaw[i] = (self.yaw[i] + sp.yaw_rate * dt + math.pi) % (2 * math.pi) - math.pi
                self.vel[i, 2] += (-sp.climb_rate - self.vel[i, 2]) * k_climb
                self._horizontal(i, None, dt)
            self.pos[i] += self.vel[i] * dt
        self._check_collisions()

    def _advance_acro(self, i: int, rc: RcOverride, dt: float) -> None:
        """Acro: стики крутят дрон, горизонт никто не держит; газ — тяга."""
        sp = rc_to_acro(rc, self.limits)
        self.tilt[i] = np.clip(self.tilt[i] + np.array([sp.roll_rate, sp.pitch_rate]) * dt,
                               -MAX_ACRO_TILT, MAX_ACRO_TILT)
        self.yaw[i] = (self.yaw[i] + sp.yaw_rate * dt + math.pi) % (2 * math.pi) - math.pi
        thrust = G * sp.throttle / self.limits.hover_throttle  # м/с² вдоль оси дрона
        roll, pitch = self.tilt[i]
        lift = thrust * math.cos(roll) * math.cos(pitch)
        self.vel[i, 2] += (G - lift - self.drag * self.vel[i, 2]) * dt  # NED: z вниз
        self._horizontal(i, thrust, dt)

    def _horizontal(self, i: int, thrust: float | None, dt: float) -> None:
        """thrust=None — AltHold: тяга подстраивается под высоту, ускорение — от наклона."""
        roll, pitch = self.tilt[i]
        if thrust is None:
            acc_forward, acc_right = G * math.tan(pitch), G * math.tan(roll)
        else:
            acc_forward = thrust * math.sin(pitch) * math.cos(roll)
            acc_right = thrust * math.sin(roll)
        c, s = math.cos(self.yaw[i]), math.sin(self.yaw[i])
        acc = np.array([acc_forward * c - acc_right * s, acc_forward * s + acc_right * c])
        self.vel[i, :2] += (acc - self.drag * self.vel[i, :2]) * dt

    def ranges(self, i: int) -> np.ndarray | None:
        """Круговой дальномер: расстояние до ближайшего здания в каждом секторе."""
        p = self.perception
        if p.lidar_range <= 0:
            return None
        bearings = (np.arange(p.sectors) + 0.5) * (2 * math.pi / p.sectors)  # центры секторов
        dirs = np.stack([np.cos(bearings), np.sin(bearings)], axis=1)
        result = np.full(p.sectors, float(p.lidar_range))
        origin = self.pos[i, :2]
        altitude = -self.pos[i, 2]
        for b in self.buildings:
            if altitude >= b.height:
                continue
            result = np.minimum(result, _ray_box(origin, dirs, b.north, b.east, p.lidar_range))
        return result

    def _check_collisions(self) -> None:
        r = self.drone_radius
        for i, p in enumerate(self.pos):
            if self.collided[i]:
                continue
            altitude = -p[2]
            hit = False
            if altitude <= 0.0:
                if self.vel[i, 2] < SOFT_LANDING:  # мягкое касание — посадка, не авария
                    self.pos[i, 2] = 0.0
                    self.vel[i] = 0.0
                else:
                    hit = True
            for b in self.buildings:
                if (b.north[0] - r <= p[0] <= b.north[1] + r
                        and b.east[0] - r <= p[1] <= b.east[1] + r and altitude < b.height):
                    hit = True
                    break
            if hit:
                self._crash(i)
        active = np.flatnonzero(~self.collided)
        for a_idx, i in enumerate(active):
            for j in active[a_idx + 1:]:
                if np.linalg.norm(self.pos[i] - self.pos[j]) < 2 * r:
                    self._crash(i)
                    self._crash(j)

    def _crash(self, i: int) -> None:
        self.collided[i] = True
        self.vel[i] = 0.0


def _ray_box(origin: np.ndarray, dirs: np.ndarray, north: tuple[float, float],
             east: tuple[float, float], max_range: float) -> np.ndarray:
    """Расстояние вдоль каждого луча до прямоугольника в плане (метод «пластин»)."""
    lo = np.array([north[0], east[0]])
    hi = np.array([north[1], east[1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (lo - origin) / dirs
        t2 = (hi - origin) / dirs
    t_near = np.nanmax(np.minimum(t1, t2), axis=1)
    t_far = np.nanmin(np.maximum(t1, t2), axis=1)
    hit = (t_far >= np.maximum(t_near, 0.0)) & (t_near <= max_range)
    t = np.where(t_near > 0, t_near, 0.0)  # внутри здания — расстояние 0
    return np.where(hit, t, max_range)
