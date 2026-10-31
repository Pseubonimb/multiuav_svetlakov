"""Cosys-AirSim здесь не запускается, поэтому проверяем перевод команд и координат на подделке клиента."""

import math
import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

import pytest

from multiuav.backends.cosys import CosysBackend
from multiuav.scenario import AlgorithmSpec, Scenario
from multiuav.types import RcOverride


class Done:
    def join(self):
        pass


class FakeClient:
    def __init__(self, ip=""):
        self.calls = []
        self.vehicles = [f"Drone{i}" for i in range(3)]

    def confirmConnection(self):
        pass

    def listVehicles(self):
        return self.vehicles

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return Done()
        return record

    def simGetCollisionInfo(self, name):
        return NS(has_collided=True, time_stamp=100)

    def simGetGroundTruthKinematics(self, name):
        v = lambda x, y, z: NS(x_val=x, y_val=y, z_val=z)  # noqa: E731
        yaw = math.radians(90)
        q = NS(w_val=math.cos(yaw / 2), x_val=0.0, y_val=0.0, z_val=math.sin(yaw / 2))
        return NS(position=v(1.0, 2.0, -10.0), linear_velocity=v(0.5, 0.0, 0.0), orientation=q)


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setitem(sys.modules, "cosysairsim", types.SimpleNamespace(MultirotorClient=FakeClient))
    sc = Scenario(name="t", drones=3, leader=AlgorithmSpec("waypoints"),
                  followers=AlgorithmSpec("single_file"), spacing=5.0, altitude=10.0)
    b = CosysBackend()
    b.start(sc)
    return b


def test_positions_are_shifted_to_common_frame(backend):
    states = backend.states()
    assert states[0].position.tolist() == [1.0, 2.0, -10.0]
    assert states[2].position.tolist() == [1.0 - 10.0, 2.0, -10.0]  # третий стартовал в 10 м позади
    assert math.isclose(states[0].yaw, math.radians(90))
    assert not states[0].collided  # касание при взлёте не считается


def test_yaw_right_is_negative_rate_for_cosys_client(backend):
    backend.send(1, RcOverride(roll=1500, pitch=2000, throttle=1500, yaw=2000))
    name, args, kwargs = backend.client.calls[-1]
    assert name == "moveByRollPitchYawrateZAsync"
    roll, pitch, yaw_rate, z, _ = args
    assert roll == 0 and pitch > 0 and yaw_rate < 0
    assert math.isclose(z, -10.0, abs_tol=1e-6)
    assert kwargs["vehicle_name"] == "Drone1"


def test_rc_channels_not_shown_by_default(backend):
    backend.send(0, RcOverride())
    assert all(name != "simPrintLogMessage" for name, _, _ in backend.client.calls)


def test_rc_channels_shown_for_leader_and_rate_limited(monkeypatch):
    monkeypatch.setitem(sys.modules, "cosysairsim", types.SimpleNamespace(MultirotorClient=FakeClient))
    sc = Scenario(name="t", drones=2, leader=AlgorithmSpec("gamepad"),
                  followers=AlgorithmSpec("single_file"), show_rc=True)
    b = CosysBackend()
    b.start(sc)
    b.send(0, RcOverride(roll=1600, pitch=1400, throttle=1500, yaw=1750))
    b.send(0, RcOverride(roll=2000, pitch=2000, throttle=2000, yaw=2000))  # сразу же — не печатаем
    b.send(1, RcOverride(roll=1000))  # ведомых не показываем
    prints = [args for name, args, _ in b.client.calls if name == "simPrintLogMessage"]
    assert prints == [("RC Drone0: ", "CH1 Roll 1600  CH2 Pitch 1400  CH3 Thr 1500  CH4 Yaw 1750  Mode ALTHOLD")]


def test_rc_single_scenario_enables_display():
    sc = Scenario.load(Path(__file__).resolve().parents[1] / "scenarios" / "rc_single.yaml")
    assert sc.drones == 1 and sc.show_rc and sc.duration_s is None
    assert not sc.stop_on_collision
    assert sc.leader.name == "gamepad"
    assert set(sc.leader.params["axes"]) == {"roll", "pitch", "throttle", "yaw"}


def test_missing_vehicles_are_reported(monkeypatch):
    class Few(FakeClient):
        def listVehicles(self):
            return ["Drone0"]

    monkeypatch.setitem(sys.modules, "cosysairsim", types.SimpleNamespace(MultirotorClient=Few))
    sc = Scenario(name="t", drones=2, leader=AlgorithmSpec("waypoints"),
                  followers=AlgorithmSpec("single_file"))
    with pytest.raises(RuntimeError, match="Drone1"):
        CosysBackend().start(sc)


def test_acro_sends_angle_rates_and_throttle(backend):
    from multiuav.types import ACRO

    backend.send(2, RcOverride(roll=2000, pitch=1500, throttle=1750, yaw=2000, mode=ACRO))
    name, args, kwargs = backend.client.calls[-1]
    assert name == "moveByAngleRatesThrottleAsync"
    roll_rate, pitch_rate, yaw_rate, throttle, _ = args
    assert roll_rate > 0 and pitch_rate == 0 and yaw_rate < 0  # рысканье — со сменой знака, как в AltHold
    assert math.isclose(throttle, 0.75)
    assert kwargs["vehicle_name"] == "Drone2"


def test_lidar_points_become_world_sectors():
    from multiuav.backends.cosys import lidar_to_ranges
    from multiuav.scenario import Perception

    yaw = math.radians(100)  # дрон смотрит почти на восток
    q = NS(w_val=math.cos(yaw / 2), x_val=0.0, y_val=0.0, z_val=math.sin(yaw / 2))
    points = np.array([[5.0, 0.0, 0.0],    # 5 м прямо по носу — значит, в секторе 90..180°
                       [3.0, 0.0, 8.0],    # 8 м ниже — земля, не препятствие
                       [0.0, 0.0, 0.0]])   # луч никуда не попал
    ranges = lidar_to_ranges(points, q, Perception(lidar_range=20, sectors=4))
    assert ranges.tolist() == [20.0, 5.0, 20.0, 20.0]  # секторы: север, восток, юг, запад


def test_settings_add_lidar_to_every_drone():
    from multiuav.cosys_settings import build_settings

    sc = Scenario(name="t", drones=3, leader=AlgorithmSpec("waypoints"),
                  followers=AlgorithmSpec("visual_follow"))
    vehicles = build_settings(sc)["Vehicles"]
    for v in vehicles.values():
        lidar = v["Sensors"]["Lidar"]
        assert lidar["SensorType"] == 6 and lidar["NumberOfChannels"] == 5


def test_fast_model_with_thrust_scale_goes_to_settings():
    from multiuav.cosys_settings import build_settings
    sc = Scenario(name="t", drones=2, leader=AlgorithmSpec("gamepad"),
                  followers=AlgorithmSpec("single_file"), model="fast2.5")
    assert {v["Model"] for v in build_settings(sc)["Vehicles"].values()} == {"Fast2.5"}
    sc.model = "generic"
    assert all("Model" not in v for v in build_settings(sc)["Vehicles"].values())


def test_rotor_drag_goes_to_settings_only_when_set():
    from multiuav.cosys_settings import build_settings
    sc = Scenario(name="t", drones=2, leader=AlgorithmSpec("gamepad"),
                  followers=AlgorithmSpec("single_file"))
    assert all("RotorDrag" not in v for v in build_settings(sc)["Vehicles"].values())
    sc.rotor_drag = 0.035
    assert {v["RotorDrag"] for v in build_settings(sc)["Vehicles"].values()} == {0.035}


def test_log_messages_hidden_by_default():
    from multiuav.cosys_settings import build_settings
    sc = Scenario(name="t", drones=2, leader=AlgorithmSpec("gamepad"),
                  followers=AlgorithmSpec("single_file"))
    assert build_settings(sc)["LogMessagesVisible"] is False
    assert build_settings(sc, messages=True)["LogMessagesVisible"] is True


def test_rate_gains_are_sent_to_every_drone(monkeypatch):
    fake = types.SimpleNamespace(MultirotorClient=FakeClient, PIDGains=lambda *g: g,
                                 AngleRateControllerGains=lambda r, p, y: (r, p, y))
    monkeypatch.setitem(sys.modules, "cosysairsim", fake)
    sc = Scenario(name="t", drones=2, leader=AlgorithmSpec("gamepad"),
                  followers=AlgorithmSpec("single_file"), rate_gains=(0.5, 0.1, 0.0))
    b = CosysBackend()
    b.start(sc)
    sent = [(a, k["vehicle_name"]) for n, a, k in b.client.calls if n == "setAngleRateControllerGains"]
    gains = ((0.5, 0.1, 0.0),) * 3  # крен, тангаж, рысканье
    assert sent == [((gains,), "Drone0"), ((gains,), "Drone1")]


def test_rate_gains_from_yaml(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("name: s\ndrones: 1\nleader: {algorithm: gamepad}\nrate_gains: {p: 0.4}\n")
    assert Scenario.load(f).rate_gains == (0.4, 0.0, 0.0)
