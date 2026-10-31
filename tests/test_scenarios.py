import dataclasses
from pathlib import Path

import pytest

from multiuav.backends.kinematic import KinematicBackend
from multiuav.cosys_settings import build_settings
from multiuav.manager import SwarmManager
from multiuav.scenario import AlgorithmSpec, Scenario

ROOT = Path(__file__).resolve().parents[1]
CORNER = ROOT / "scenarios" / "corner.yaml"


def run(algorithm: str, drones: int):
    base = Scenario.load(CORNER)
    sc = dataclasses.replace(base, drones=drones,
                             followers=AlgorithmSpec(algorithm, base.followers.params))
    return SwarmManager(sc, KinematicBackend()).run()


def test_leader_alone_reaches_finish():
    result = run("single_file", 1)
    assert result.finished.keys() == {0}
    assert not result.collided


def test_single_file_cuts_the_corner_with_long_column():
    # Ровно то, что видно на практике: хвост длинной колонны срезает угол.
    result = run("single_file", 10)
    assert result.collided
    assert 0 not in result.collided


@pytest.mark.parametrize("drones", [3, 5, 10])
def test_leader_path_passes_the_corner(drones):
    result = run("leader_path", drones)
    assert not result.collided
    assert len(result.finished) == drones


def test_custom_algorithm_from_file():
    result = run(f"{ROOT / 'examples' / 'my_algorithm.py'}:Cautious", 3)
    assert 0 in result.finished


def test_cosys_settings_match_start_positions():
    sc = Scenario.load(CORNER)
    vehicles = build_settings(sc)["Vehicles"]
    assert list(vehicles) == [f"Drone{i}" for i in range(sc.drones)]
    assert vehicles["Drone2"]["X"] == -12.0  # колонна выстроена позади ведущего


@pytest.mark.parametrize("raw", [None, 0])
def test_duration_null_or_zero_means_unlimited(raw):
    data = {"leader": {"algorithm": "waypoints"}, "duration_s": raw}
    assert Scenario.from_dict(data).duration_s is None


def test_ctrl_c_ends_unlimited_run_with_result():
    class StopAfter(KinematicBackend):
        calls = 0

        def states(self):
            self.calls += 1
            if self.calls > 50:
                raise KeyboardInterrupt
            return super().states()

    sc = dataclasses.replace(Scenario.load(CORNER), drones=2, duration_s=None)
    result = SwarmManager(sc, StopAfter()).run()
    assert result.sim_time > 2.0 and result.track


def test_manual_flight_continues_after_collision():
    base = Scenario.load(CORNER)
    sc = dataclasses.replace(base, drones=10, duration_s=60.0, stop_on_collision=False,
                             followers=AlgorithmSpec("single_file", base.followers.params))
    result = SwarmManager(sc, KinematicBackend()).run()
    assert result.collided  # столкновения записаны...
    assert result.sim_time >= 60.0  # ...но прогон не оборвался


def test_track_csv_has_rc_channels(tmp_path):
    import csv

    result = run("leader_path", 3)
    rows = list(csv.DictReader(open(result.save(tmp_path) / "track.csv", encoding="utf-8")))
    assert {"rc_roll", "rc_pitch", "rc_throttle", "rc_yaw"} <= set(rows[0])
    first = rows[0]
    assert all(1000 <= int(first[k]) <= 2000 for k in ("rc_roll", "rc_pitch", "rc_throttle", "rc_yaw"))
    assert rows[-1]["rc_roll"] == ""  # последний шаг: все финишировали, команд уже нет
