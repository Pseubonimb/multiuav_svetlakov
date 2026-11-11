"""Ручное управление ведущим с геймпада (раскладка Mode 2, как на пульте)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..types import ACRO, ALTHOLD, MODES, DroneState, RcOverride
from .base import FollowerAlgorithm

# Оси по умолчанию для геймпада Xbox под Linux. Свои номера осей
# можно узнать командой `python -m multiuav gamepad` и задать в params.
DEFAULT_AXES = {"yaw": 0, "throttle": 1, "roll": 3, "pitch": 4}

#: Коды осей-«крестовин» (ABS_HAT0X…ABS_HAT3Y): SDL делает из них hat, а не оси.
_HAT_CODES = range(0x10, 0x18)


def axes_from_sticks_map(path: str | Path, abs_codes: list[int] | None = None
                         ) -> tuple[str, dict[str, int], list[str]]:
    """Разметка `sticks map` (коды evdev) → (имя пульта, номера осей pygame, инвертируемые оси).

    SDL нумерует оси по возрастанию кода evdev, пропуская крестовины. abs_codes — коды
    осей устройства (из evdev); без них номер оси считается равным коду (у RadioMaster
    коды 0–7 идут подряд).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    codes = sorted(c for c in (abs_codes or []) if c not in _HAT_CODES)
    axes, invert = {}, []
    for stick, m in data["axes"].items():
        code = int(m["code"])
        axes[stick] = codes.index(code) if code in codes else code
        # В разметке inverted — «вверх/вправо даёт Min»; у pygame Min = -1, а нам вверх = +1.
        if m.get("inverted"):
            invert.append(stick)
    return data.get("device_name", ""), axes, invert


def _device_abs_codes(name: str) -> list[int] | None:
    try:
        import evdev
    except ImportError:
        return None
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if dev.name == name:
            return [code for code, _ in dev.capabilities().get(evdev.ecodes.EV_ABS, [])]
    return None


class GamepadPilot(FollowerAlgorithm):
    """Левый стик: вверх-вниз — высота, влево-вправо — поворот.
    Правый стик: вверх-вниз — вперёд-назад, влево-вправо — крен.

    Параметры: axes — номера осей {roll, pitch, throttle, yaw};
    invert — оси, которые надо перевернуть (по умолчанию pitch и throttle,
    потому что у геймпадов «вверх» — это минус); deadzone — мёртвая зона (0.08);
    joystick — номер геймпада (0);
    arm_low_throttle — в Acro моторы молчат, пока газ хоть раз не опустили в ноль (false);
    sticks_map — файл разметки `python -m multiuav sticks map` (radio/sticks_map.json): оси,
    инверсия и сам пульт (по имени) берутся оттуда — та же разметка, что у записи стиков;
    axes и invert тогда не нужны;
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
        sticks_map = self.param("sticks_map", None)
        name = ""
        if sticks_map:
            name, axes, invert = axes_from_sticks_map(sticks_map)
            _, axes, invert = axes_from_sticks_map(sticks_map, _device_abs_codes(name))
            names = [pygame.joystick.Joystick(i).get_name() for i in range(pygame.joystick.get_count())]
            if name in names:
                index = names.index(name)
            elif names:
                raise RuntimeError(f"Пульт «{name}» из {sticks_map} не найден, есть: {', '.join(names)}")
        if pygame.joystick.get_count() <= index:
            raise RuntimeError("Пульт не найден: подключите его по USB (режим USB Joystick) "
                               "и проверьте: python -m multiuav gamepad")
        self.joystick = pygame.joystick.Joystick(index)
        self.joystick.init()
        if sticks_map:
            self.axes, self.invert = axes, set(invert)
        else:
            self.axes = {**DEFAULT_AXES, **self.param("axes", {})}
            self.invert = set(self.param("invert", ["pitch", "throttle"]))
        self.deadzone = float(self.param("deadzone", 0.08))
        self.mode = str(self.param("mode", ALTHOLD))
        if self.mode not in MODES:
            raise ValueError(f"Неизвестный режим '{self.mode}'. Есть: {', '.join(MODES)}")
        self.mode_switch = self.param("mode_switch", None)
        # Как «арминг» в FPV: в Acro моторы молчат, пока газ хоть раз не опустили в ноль.
        # Защищает от взлёта на полном газе (газ оставлен вверху или разметка перевёрнута).
        self.armed = not bool(self.param("arm_low_throttle", False))

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
        mode = self._mode()
        throttle = self._axis("throttle")
        if mode == ACRO and not self.armed:
            if throttle > -0.95:
                throttle = -1.0  # ждём, пока газ опустят
            else:
                self.armed = True
        return RcOverride.from_normalized(
            roll=self._axis("roll"), pitch=self._axis("pitch"),
            throttle=throttle, yaw=self._axis("yaw"), mode=mode)
