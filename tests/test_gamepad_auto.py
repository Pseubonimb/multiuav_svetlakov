"""Выбор геймпада по имени и числу осей (без настоящего геймпада: pygame подменяется)."""

from __future__ import annotations

import sys
import types

import pytest

from multiuav.algorithms import load_algorithm
from multiuav.control import ControlLimits

AUTO = "multiuav.algorithms.gamepad_auto:AutoGamepadPilot"


def fake_pygame(monkeypatch, devices):
    """devices: [(имя, [значения осей])] — что «видит» SDL."""

    class Joystick:
        def __init__(self, i):
            self.name, self.values = devices[i]

        def init(self):
            pass

        def get_name(self):
            return self.name

        def get_numaxes(self):
            return len(self.values)

        def get_axis(self, i):
            return self.values[i]

    pg = types.SimpleNamespace(
        init=lambda: None,
        joystick=types.SimpleNamespace(init=lambda: None, get_count=lambda: len(devices),
                                       Joystick=Joystick),
        event=types.SimpleNamespace(pump=lambda: None))
    monkeypatch.setitem(sys.modules, "pygame", pg)


def make(params):
    return load_algorithm(AUTO)(0, params, ControlLimits())


KEYBOARD = ("COMPANY USB Device Keyboard", [-1.0, -1.0])
XBOX = ("8BitDo Ultimate Wireless / Pro 2 Wired Controller", [0, 0, -1, 0, 0, -1])
SWITCH = ("Nintendo Co., Ltd. Pro Controller", [0, 0, 0, 0])
MSI = ("MSI GC30 V2", [0, 0, -1, 0, 0, -1])


@pytest.mark.parametrize("order", [[KEYBOARD, XBOX], [XBOX, KEYBOARD]])
def test_skips_keyboard_whatever_the_order(monkeypatch, order):
    fake_pygame(monkeypatch, order)
    pilot = make({})
    assert pilot.joystick.get_name() == XBOX[0]
    assert pilot.axes == {"yaw": 0, "throttle": 1, "roll": 3, "pitch": 4}


def test_switch_mode_layout(monkeypatch):
    fake_pygame(monkeypatch, [KEYBOARD, SWITCH])
    pilot = make({})
    assert pilot.joystick.get_name() == SWITCH[0]
    assert pilot.axes == {"yaw": 0, "throttle": 1, "roll": 2, "pitch": 3}


def test_by_name_and_explicit_axes(monkeypatch):
    fake_pygame(monkeypatch, [KEYBOARD, XBOX, MSI])
    pilot = make({"name": "msi", "axes": {"roll": 2, "pitch": 5}})
    assert pilot.joystick.get_name() == MSI[0]
    assert pilot.axes["roll"] == 2 and pilot.axes["pitch"] == 5 and pilot.axes["yaw"] == 0


def test_stick_forward_is_pitch_forward(monkeypatch):
    # Правый стик вверх (ось 3 у Switch Pro = -1) — вперёд, как у обычного gamepad.
    fake_pygame(monkeypatch, [KEYBOARD, ("Nintendo Co., Ltd. Pro Controller", [0, 0, 0, -1.0])])
    rc = make({}).step(None, [], None, 0.0)
    assert rc.pitch > 1500 and rc.roll == 1500


def test_clear_error_when_no_gamepad(monkeypatch):
    fake_pygame(monkeypatch, [KEYBOARD])
    with pytest.raises(RuntimeError, match="Keyboard"):
        make({"wait_s": 0})


def test_waits_for_sleeping_gamepad(monkeypatch):
    devices = [KEYBOARD]
    fake_pygame(monkeypatch, devices)
    import multiuav.algorithms.gamepad_auto as ga

    def wake(_):  # пока ведущий ждёт, геймпад «просыпается»
        devices.append(XBOX)

    monkeypatch.setattr(ga.time, "sleep", wake)
    pilot = make({"wait_s": 5})
    assert pilot.joystick.get_name() == XBOX[0]


def test_disconnect_mid_flight_gives_neutral_then_resumes(monkeypatch):
    devices = [KEYBOARD, (XBOX[0], [0, 0, -1, 0, -1.0, -1])]  # правый стик вверх до упора
    fake_pygame(monkeypatch, devices)
    import multiuav.algorithms.gamepad_auto as ga

    clock = [0.0]
    monkeypatch.setattr(ga.time, "monotonic", lambda: clock[0])
    pilot = make({})
    assert pilot.step(None, [], None, 0.0).pitch == 2000

    devices.pop(1)          # геймпад уснул
    clock[0] += 1.5
    rc = pilot.step(None, [], None, 1.5)
    assert (rc.roll, rc.pitch, rc.throttle, rc.yaw) == (1500, 1500, 1500, 1500)

    devices.insert(0, (XBOX[0], [0, 0, -1, 0, -1.0, -1]))   # снова подключился, уже под номером 0
    clock[0] += 1.5
    assert pilot.step(None, [], None, 3.0).pitch == 2000
