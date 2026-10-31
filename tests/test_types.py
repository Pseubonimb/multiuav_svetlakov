import math

import numpy as np

from multiuav.control import ControlLimits, VelocityController, rc_to_setpoint
from multiuav.types import DroneState, RcOverride


def test_rc_roundtrip():
    rc = RcOverride.from_normalized(roll=1.0, pitch=-0.5, throttle=0.0, yaw=2.0)
    assert rc == RcOverride(2000, 1250, 1500, 2000)
    assert rc.normalized() == (1.0, -0.5, 0.0, 1.0)


def test_rc_to_setpoint_full_stick():
    limits = ControlLimits()
    sp = rc_to_setpoint(RcOverride(2000, 1000, 2000, 1500), limits)
    assert math.isclose(sp.roll, limits.max_tilt)
    assert math.isclose(sp.pitch, -limits.max_tilt)
    assert math.isclose(sp.climb_rate, limits.max_climb)
    assert sp.yaw_rate == 0


def test_velocity_controller_pitches_forward_toward_target():
    me = DroneState(1, np.array([0.0, 0.0, -10.0]), np.zeros(3), yaw=0.0)
    rc = VelocityController().command(me, np.array([5.0, 0.0]), yaw=0.0, altitude=10.0)
    assert rc.pitch > 1500  # вперёд = на север при курсе 0
    assert rc.roll == 1500
    assert rc.throttle == 1500


def test_velocity_controller_rolls_right_for_east_when_facing_north():
    me = DroneState(1, np.array([0.0, 0.0, -10.0]), np.zeros(3), yaw=0.0)
    rc = VelocityController().command(me, np.array([0.0, 5.0]), yaw=None, altitude=12.0)
    assert rc.roll > 1500
    assert rc.throttle > 1500
