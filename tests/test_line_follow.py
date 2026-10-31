"""line_follow: ведомые «по пути ведущего» — только своя одометрия и передняя камера."""

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from multiuav.algorithms import load_algorithm
from multiuav.algorithms.line_follow import LineFollow
from multiuav.backends.kinematic import KinematicBackend
from multiuav.manager import SwarmManager
from multiuav.scenario import Scenario
from multiuav.types import DroneState

ROOT = Path(__file__).resolve().parents[1]


def run(name: str, drones: int):
    sc = dataclasses.replace(Scenario.load(ROOT / "scenarios" / f"{name}.yaml"), drones=drones)
    return SwarmManager(sc, KinematicBackend()).run()


def test_registered_by_name():
    assert load_algorithm("line_follow") is LineFollow


@pytest.mark.parametrize("drones", [3, 5, 10])
def test_corner_without_collisions(drones):
    result = run("line_corner", drones)
    assert not result.collided
    assert len(result.finished) == drones


def test_city_with_leader_stop():
    """Три поворота и остановка ведущего посреди улицы: никто ни во что не врезался."""
    result = run("line_city", 5)
    assert not result.collided
    assert len(result.finished) == 5


def test_sharp_turn_135_degrees():
    """Поворот на 135° в открытом поле: сосед уходит за край кадра передней камеры,
    ведомый поворачивается туда, куда тот летел, и колонна проходит поворот целиком."""
    import math
    end = [60 + 150 * math.cos(math.radians(135)), 150 * math.sin(math.radians(135)), 10]
    sc = Scenario.from_dict({
        "name": "turn135", "drones": 8, "rate_hz": 20, "duration_s": 120,
        "start": {"north": 0, "east": 0, "altitude": 10, "heading_deg": 0, "spacing": 6},
        "leader": {"algorithm": "waypoints", "params": {"speed": 6, "waypoints": [[60, 0, 10], end]}},
        "followers": {"algorithm": "line_follow", "params": {"distance": 6, "clock": "sim"}},
        "perception": {"lidar_range": 40, "vision_range": 40, "fov_deg": 140, "noise": 0.03},
    })
    result = SwarmManager(sc, KinematicBackend()).run()
    assert not result.collided
    tail = [row for row in result.track if row[1] == 7][-1]
    assert tail[3] > 30   # последний ведомый ушёл по второй улице на восток


def _state(i, north, east, v_north=3.0, ranges=None):
    return DroneState(i, np.array([north, east, -10.0]), np.array([v_north, 0.0, 0.0]),
                      0.0, False, ranges)


def test_uses_neither_ids_nor_leader_nor_lidar():
    """Номера соседей, их порядок, аргумент leader и дальномер не влияют на команды:
    алгоритм берёт из наблюдений только направление и расстояние."""
    params = {"clock": "sim", "seed": 3}
    a, b = LineFollow(5, params), LineFollow(5, params)
    rng = np.random.default_rng(0)
    for k in range(60):
        t = k * 0.05
        me = _state(5, 3.0 * t, 0.0)
        seen = [_state(4, 6.0 + 3.0 * t, 0.1), _state(3, 12.0 + 3.0 * t, -0.1)]
        cmd_a = a.step(me, seen, seen[-1], t)
        other_ids = [dataclasses.replace(s, id=int(rng.integers(100, 999))) for s in seen[::-1]]
        me_b = dataclasses.replace(me, ranges=rng.uniform(0, 5, 72))
        cmd_b = b.step(me_b, other_ids, None, t)
        assert cmd_a == cmd_b
