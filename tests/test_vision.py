"""Режимы пульта, дальномер, зрение и алгоритмы без связи — в упрощённом симуляторе."""

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from multiuav.__main__ import _load, main
from multiuav.algorithms.base import FollowerAlgorithm
from multiuav.backends.kinematic import KinematicBackend
from multiuav.control import ControlLimits, rc_to_acro
from multiuav.manager import SwarmManager
from multiuav.perception import Observer, can_see
from multiuav.scenario import AlgorithmSpec, Building, Perception, Scenario
from multiuav.types import ACRO, DroneState, RcOverride

ROOT = Path(__file__).resolve().parents[1]
CORNER = ROOT / "scenarios" / "corner.yaml"


def corner(algorithm: str, drones: int):
    base = Scenario.load(CORNER)
    sc = dataclasses.replace(base, drones=drones,
                             followers=AlgorithmSpec(algorithm, base.followers.params))
    return SwarmManager(sc, KinematicBackend()).run()


@pytest.mark.parametrize("algorithm", ["visual_follow", "flock"])
@pytest.mark.parametrize("drones", [3, 5, 10])
def test_vision_algorithms_pass_the_corner(algorithm, drones):
    result = corner(algorithm, drones)
    assert not result.collided
    assert len(result.finished) == drones


def test_rc_to_acro():
    sp = rc_to_acro(RcOverride(2000, 1000, 1000, 1500, ACRO), ControlLimits())
    assert math.isclose(sp.roll_rate, math.radians(200))
    assert math.isclose(sp.pitch_rate, -math.radians(200))
    assert sp.throttle == 0.0 and sp.yaw_rate == 0.0


def _one_drone(**kwargs) -> tuple[KinematicBackend, Scenario]:
    sc = Scenario(name="t", drones=1, leader=AlgorithmSpec("waypoints"),
                  followers=AlgorithmSpec("single_file"), altitude=20.0, **kwargs)
    backend = KinematicBackend()
    backend.start(sc)
    return backend, sc


def _fly(backend, rc, seconds):
    for _ in range(int(seconds * 20)):
        backend.send(0, rc)
        backend.advance(0.05)


def test_acro_hover_climb_and_fall():
    backend, sc = _one_drone()
    hover = RcOverride.from_normalized(throttle=2 * sc.limits.hover_throttle - 1, mode=ACRO)
    _fly(backend, hover, 2.0)
    assert abs(-backend.pos[0, 2] - 20.0) < 0.5  # газ висения — высота держится
    _fly(backend, RcOverride.from_normalized(throttle=1.0, mode=ACRO), 1.0)
    assert -backend.pos[0, 2] > 22.0  # полный газ — вверх
    backend, _ = _one_drone()
    _fly(backend, RcOverride.from_normalized(throttle=-1.0, mode=ACRO), 1.0)
    assert -backend.pos[0, 2] < 16.0  # моторы стоп — падаем


def test_acro_does_not_self_level():
    backend, sc = _one_drone()
    hover = 2 * sc.limits.hover_throttle - 1
    _fly(backend, RcOverride.from_normalized(roll=0.5, throttle=hover, mode=ACRO), 0.2)
    tilt = backend.tilt[0, 0]
    _fly(backend, RcOverride.from_normalized(throttle=hover, mode=ACRO), 1.0)
    assert tilt > 0.2 and math.isclose(backend.tilt[0, 0], tilt)  # стик в центре — крен остаётся
    # а в AltHold центр стика возвращает дрон в горизонт
    _fly(backend, RcOverride(), 1.0)
    assert abs(backend.tilt[0, 0]) < 0.01


def test_ranges_see_buildings_below_roof_only():
    wall = Building(north=(10, 20), east=(-50, 50), height=30)
    backend, _ = _one_drone(buildings=[wall], perception=Perception(lidar_range=25, sectors=36))
    ranges = backend.ranges(0)
    assert math.isclose(ranges[0], 10 / math.cos(math.radians(5)), rel_tol=1e-6)  # сектор 0..10° на север
    assert ranges[18] == 25  # на юг пусто
    backend.pos[0, 2] = -40  # выше крыши
    assert (backend.ranges(0) == 25).all()


def test_building_hides_neighbour():
    me = DroneState(1, np.array([0.0, 0.0, -10]), np.zeros(3), 0.0,
                    ranges=np.full(72, 20.0))
    other = DroneState(0, np.array([15.0, 0.0, -10]), np.zeros(3), 0.0)
    p = Perception()
    assert can_see(me, other, p)
    me.ranges[0] = 5.0  # на север в 5 м стена
    assert not can_see(me, other, p)
    far = DroneState(0, np.array([0.0, 50.0, -10]), np.zeros(3), 0.0)
    assert not can_see(me, far, p)  # дальше vision_range
    behind = DroneState(0, np.array([-10.0, 0.0, -10]), np.zeros(3), 0.0)
    assert not can_see(me, behind, Perception(fov_deg=120))


def test_vision_algorithms_get_only_what_they_see():
    seen_by_follower = []

    class Spy(FollowerAlgorithm):
        vision_only = True

        def step(self, me, others, leader, t):
            seen_by_follower.append(({s.id for s in others}, leader))
            return RcOverride()

    import multiuav.algorithms as algorithms
    algorithms.BUILTIN["spy"] = Spy
    try:
        far = Scenario(name="t", drones=2, leader=AlgorithmSpec("waypoints"),
                       followers=AlgorithmSpec("spy"), spacing=100.0, duration_s=0.1)
        SwarmManager(far, KinematicBackend()).run()
    finally:
        del algorithms.BUILTIN["spy"]
    assert seen_by_follower and all(ids == set() and leader is None
                                    for ids, leader in seen_by_follower)


def test_observer_adds_noise_but_keeps_ids():
    me = DroneState(1, np.zeros(3), np.zeros(3), 0.0)
    other = DroneState(0, np.array([20.0, 0.0, 0.0]), np.zeros(3), 0.0)
    seen, leader = Observer(1, Perception(noise=0.05)).observe(me, [me, other])
    assert leader is not None and leader.id == 0
    assert 0 < np.linalg.norm(leader.position - other.position) < 5


def test_followers_option_sets_group_size():
    args = type("A", (), {"scenario": str(ROOT / "scenarios" / "rc_group.yaml"),
                          "followers": 0, "mode": "acro"})()
    sc = _load(args)
    assert sc.drones == 1
    assert sc.leader.params["mode"] == "acro"
    assert sc.followers.name == "visual_follow" and not sc.stop_on_collision


def test_cli_compare_runs_vision_algorithms(capsys):
    main(["compare", str(CORNER), "--algorithms", "visual_follow", "--drones", "3"])
    assert "visual_follow" in capsys.readouterr().out
