"""Общее, пришедшее с ветки эксперимента: разметка пульта, кнопки, холостой ход, касания."""

import json
import math
import os
import tempfile

from multiuav.algorithms.gamepad import axes_from_sticks_map
from multiuav.backends.kinematic import KinematicBackend
from multiuav.control import ControlLimits, rc_to_acro
from multiuav.scenario import AlgorithmSpec, Scenario
from multiuav.sticklog import ButtonWriter
from multiuav.types import ACRO, RcOverride


def test_button_writer_rows_and_autorepeat():
    with tempfile.TemporaryFile("w+", encoding="utf-8") as f:
        w = ButtonWriter(f, {304: "SE"})
        assert w.key_event(1000, 999_500, 304, 1)
        assert not w.key_event(1001, 999_600, 304, 2)  # автоповтор — не событие
        assert w.key_event(1100, 1_099_500, 304, 0)
        assert w.key_event(1200, 1_199_500, 305, 1, "BTN_B")
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        lines = os.read(f.fileno(), 4096).decode().splitlines()
    assert lines == ["t_ms,evdev_us,button,code,state", "1000,999500,SE,304,1",
                     "1100,1099500,SE,304,0", "1200,1199500,BTN_B,305,1"]


def test_sticks_map_gives_pygame_axes(tmp_path):
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"device_name": "Pult", "axes": {
        "throttle": {"code": 2, "inverted": False}, "yaw": {"code": 3, "inverted": True},
        "pitch": {"code": 1}, "roll": {"code": 0}}}))
    name, axes, invert = axes_from_sticks_map(path)
    assert name == "Pult" and axes == {"throttle": 2, "yaw": 3, "pitch": 1, "roll": 0}
    assert invert == ["yaw"]
    # SDL пропускает крестовины (ABS_HAT0X = 0x10) и нумерует остальные оси подряд
    _, axes, _ = axes_from_sticks_map(path, [0, 1, 0x10, 2, 3])
    assert axes["throttle"] == 2 and axes["yaw"] == 3


def test_idle_throttle_keeps_motors_spinning():
    limits = ControlLimits(idle_throttle=0.055)
    low = rc_to_acro(RcOverride.from_normalized(throttle=-1.0, mode=ACRO), limits)
    high = rc_to_acro(RcOverride.from_normalized(throttle=1.0, mode=ACRO), limits)
    assert math.isclose(low.throttle, 0.055) and math.isclose(high.throttle, 1.0)


def test_kinematic_touching_only_at_the_moment_of_impact():
    sc = Scenario(name="t", drones=1, leader=AlgorithmSpec("waypoints"),
                  followers=AlgorithmSpec("single_file"), altitude=20.0)
    backend = KinematicBackend()
    backend.start(sc)
    backend.collided[0] = True
    assert backend.states()[0].touching
    assert not backend.states()[0].touching  # следующий шаг — уже не новое касание
    backend.reset(0, [5.0, 0.0, 10.0], 0.0)
    state = backend.states()[0]
    assert not state.collided and math.isclose(state.altitude, 10.0)
