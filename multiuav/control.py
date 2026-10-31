"""Перевод RC-команд в физические уставки и помощник для алгоритмов.

`rc_to_setpoint` — единое для всех симуляторов толкование стиков.
`VelocityController` — для авторов алгоритмов, которым удобнее думать
скоростями, а не стиками: «хочу лететь с такой скоростью, таким курсом,
на такой высоте» -> RcOverride.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .types import DroneState, RcOverride, wrap_angle

G = 9.81


@dataclass(frozen=True)
class ControlLimits:
    """Что означает полное отклонение стика."""

    max_tilt: float = math.radians(30)  # крен/тангаж, рад
    max_yaw_rate: float = math.radians(90)  # рад/с
    max_climb: float = 3.0  # вертикальная скорость, м/с
    max_rate: float = math.radians(200)  # Acro: угловая скорость крена/тангажа при полном стике, рад/с
    hover_throttle: float = 0.5  # Acro: доля тяги, при которой дрон висит (для упрощённого симулятора)


@dataclass(frozen=True)
class Setpoint:
    roll: float  # рад, > 0 — вправо
    pitch: float  # рад, > 0 — вперёд
    yaw_rate: float  # рад/с, > 0 — по часовой
    climb_rate: float  # м/с, > 0 — вверх


@dataclass(frozen=True)
class AcroSetpoint:
    roll_rate: float  # рад/с, > 0 — крениться вправо
    pitch_rate: float  # рад/с, > 0 — наклоняться вперёд
    yaw_rate: float  # рад/с, > 0 — по часовой
    throttle: float  # тяга 0..1


def rc_to_setpoint(rc: RcOverride, limits: ControlLimits) -> Setpoint:
    """AltHold: стики — углы наклона, газ — скорость подъёма."""
    roll, pitch, throttle, yaw = rc.normalized()
    return Setpoint(
        roll=roll * limits.max_tilt,
        pitch=pitch * limits.max_tilt,
        yaw_rate=yaw * limits.max_yaw_rate,
        climb_rate=throttle * limits.max_climb,
    )


def rc_to_acro(rc: RcOverride, limits: ControlLimits) -> AcroSetpoint:
    """Acro: стики — угловые скорости, газ — тяга (низ стика — 0, верх — 1)."""
    roll, pitch, throttle, yaw = rc.normalized()
    return AcroSetpoint(
        roll_rate=roll * limits.max_rate,
        pitch_rate=pitch * limits.max_rate,
        yaw_rate=yaw * limits.max_rate,
        throttle=(throttle + 1.0) / 2.0,
    )


@dataclass
class VelocityController:
    """Пропорциональный регулятор: желаемая скорость, курс и высота -> RcOverride."""

    limits: ControlLimits = ControlLimits()
    k_vel: float = 1.5  # 1/с: ошибка скорости -> ускорение
    k_yaw: float = 2.5  # 1/с: ошибка курса -> угловая скорость
    k_alt: float = 1.0  # 1/с: ошибка высоты -> вертикальная скорость

    def command(self, me: DroneState, velocity_ne: np.ndarray, yaw: float | None,
                altitude: float) -> RcOverride:
        """velocity_ne — желаемая горизонтальная скорость (север, восток), м/с.
        yaw — желаемый курс, рад; None — не поворачивать."""
        err = np.asarray(velocity_ne, dtype=float)[:2] - me.velocity[:2]
        c, s = math.cos(me.yaw), math.sin(me.yaw)
        acc_forward = self.k_vel * (err[0] * c + err[1] * s)
        acc_right = self.k_vel * (-err[0] * s + err[1] * c)
        tilt = self.limits.max_tilt
        pitch = math.atan2(acc_forward, G) / tilt
        roll = math.atan2(acc_right, G) / tilt

        yaw_cmd = 0.0
        if yaw is not None:
            yaw_cmd = self.k_yaw * wrap_angle(yaw - me.yaw) / self.limits.max_yaw_rate

        climb = self.k_alt * (altitude - me.altitude) / self.limits.max_climb
        return RcOverride.from_normalized(roll=roll, pitch=pitch, throttle=climb, yaw=yaw_cmd)
