"""Ручное управление ведущим с геймпада (раскладка Mode 2, как на пульте)."""

from __future__ import annotations

import os

from ..types import ACRO, ALTHOLD, MODES, DroneState, RcOverride
from .base import FollowerAlgorithm

# Оси по умолчанию для геймпада Xbox под Linux. Свои номера осей
# можно узнать командой `python -m multiuav gamepad` и задать в params.
DEFAULT_AXES = {"yaw": 0, "throttle": 1, "roll": 3, "pitch": 4}


class GamepadPilot(FollowerAlgorithm):
    """Левый стик: вверх-вниз — высота, влево-вправо — поворот.
    Правый стик: вверх-вниз — вперёд-назад, влево-вправо — крен.

    Параметры: axes — номера осей {roll, pitch, throttle, yaw};
    invert — оси, которые надо перевернуть (по умолчанию pitch и throttle,
    потому что у геймпадов «вверх» — это минус); deadzone — мёртвая зона (0.08);
    joystick — номер геймпада (0);
    mode — режим полёта: althold (по умолчанию) или acro;
    mode_switch — переключатель режима на пульте: {axis: номер оси, acro_above: 0.0} —
    если ось больше порога, летим в Acro, иначе в AltHold (как тумблер CH5).

    В Acro газ — это тяга: низ стика — моторы стоп, верх — полная тяга.
    Самоцентрирующийся стик газа геймпада для Acro неудобен — лучше пульт.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame

        self._pygame = pygame
        pygame.init()
        pygame.joystick.init()
        index = int(self.param("joystick", 0))
        if pygame.joystick.get_count() <= index:
            raise RuntimeError("Геймпад не найден. Подключи его и проверь: python -m multiuav gamepad")
        self.joystick = pygame.joystick.Joystick(index)
        self.joystick.init()
        self.axes = {**DEFAULT_AXES, **self.param("axes", {})}
        self.invert = set(self.param("invert", ["pitch", "throttle"]))
        self.deadzone = float(self.param("deadzone", 0.08))
        self.mode = str(self.param("mode", ALTHOLD))
        if self.mode not in MODES:
            raise ValueError(f"Неизвестный режим '{self.mode}'. Есть: {', '.join(MODES)}")
        self.mode_switch = self.param("mode_switch", None)

    def _axis(self, name: str) -> float:
        value = self.joystick.get_axis(int(self.axes[name]))
        # В Acro газ — тяга, мёртвая зона в центре стика газа там не нужна.
        if abs(value) < self.deadzone and not (name == "throttle" and self._mode() == ACRO):
            value = 0.0
        return -value if name in self.invert else value

    def _mode(self) -> str:
        if not self.mode_switch:
            return self.mode
        value = self.joystick.get_axis(int(self.mode_switch["axis"]))
        return ACRO if value > float(self.mode_switch.get("acro_above", 0.0)) else ALTHOLD

    def step(self, me: DroneState, others: list[DroneState], leader: DroneState,
             t: float) -> RcOverride:
        self._pygame.event.pump()
        return RcOverride.from_normalized(
            roll=self._axis("roll"), pitch=self._axis("pitch"),
            throttle=self._axis("throttle"), yaw=self._axis("yaw"), mode=self._mode())
