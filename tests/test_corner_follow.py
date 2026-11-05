"""corner_follow: ведомый без одометрии — только передняя камера, гироскоп и высотомер."""

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from multiuav.algorithms import load_algorithm
from multiuav.algorithms.corner_follow import CornerFollow
from multiuav.backends.kinematic import KinematicBackend
from multiuav.manager import SwarmManager
from multiuav.scenario import AlgorithmSpec, Scenario
from multiuav.types import DroneState

ROOT = Path(__file__).resolve().parents[1]


def run(name: str, drones: int):
    sc = Scenario.load(ROOT / "scenarios" / f"{name}.yaml")
    sc = dataclasses.replace(sc, drones=drones,
                             followers=AlgorithmSpec("corner_follow", {"distance": 6, "seed": 0}))
    return SwarmManager(sc, KinematicBackend()).run()


def test_registered_by_name():
    assert load_algorithm("corner_follow") is CornerFollow


@pytest.mark.parametrize("drones", [5, 10])
def test_corner_without_collisions(drones):
    result = run("line_corner", drones)
    assert not result.collided
    assert len(result.finished) == drones


def test_city_with_leader_stop():
    """Три поворота и остановка ведущего посреди улицы."""
    result = run("line_city", 5)
    assert not result.collided
    assert len(result.finished) == 5


def _state(i, north, east, yaw=0.0):
    return DroneState(i, np.array([north, east, -10.0]), np.zeros(3), yaw, False, None)


def test_knows_neither_own_position_nor_speed():
    """Нет одометрии: если сдвинуть всю картину (своё положение и соседей вместе) и
    подменить свою скорость, номера соседей и аргумент leader, команды не меняются —
    алгоритм видит только то, где соседи относительно него."""
    a, b = CornerFollow(5, {"seed": 3}), CornerFollow(5, {"seed": 3})
    rng = np.random.default_rng(0)
    shift = np.array([37.0, -112.0, 0.0])
    for k in range(80):
        t = k * 0.05
        me = _state(5, 2.0 * t, 0.0)
        # сосед впереди сворачивает направо
        north = min(6.0 + 2.0 * t, 10.0)
        east = max(0.0, 2.0 * t - 4.0)
        seen = [_state(4, north, east, yaw=1.5 if east > 0 else 0.0)]
        cmd_a = a.step(me, seen, seen[0], t)
        me_b = dataclasses.replace(me, position=me.position + shift,
                                   velocity=rng.uniform(-5, 5, 3))
        seen_b = [dataclasses.replace(s, id=int(rng.integers(100, 999)),
                                      position=s.position + shift) for s in seen]
        cmd_b = b.step(me_b, seen_b, None, t)
        assert cmd_a == cmd_b


def test_rangefinder_option_with_noisy_camera():
    """walls: true (если разрешён дальномер): ошибка камеры 5 %, 10 дронов, город —
    ни одного столкновения (без дальномера при такой ошибке хвост колонны бьётся)."""
    sc = Scenario.load(ROOT / "scenarios" / "line_city.yaml")
    sc = dataclasses.replace(
        sc, drones=10, perception=dataclasses.replace(sc.perception, noise=0.05),
        followers=AlgorithmSpec("corner_follow", {"distance": 6, "seed": 0, "walls": True}))
    result = SwarmManager(sc, KinematicBackend()).run()
    assert not result.collided
    assert len(result.finished) == 10


def _run_low_drag(air_drag: float):
    sc = Scenario.load(ROOT / "scenarios" / "line_blocks.yaml")
    params = {k: v for k, v in sc.followers.params.items() if k != "clock"}
    sc = dataclasses.replace(sc, drones=7, followers=AlgorithmSpec("corner_follow", params),
                             air_drag=air_drag, duration_s=150.0)
    return SwarmManager(sc, KinematicBackend(drag=0.02)).run()


def test_air_drag_restores_column_in_scene_without_drag():
    """Сцена без сопротивления воздуха (готовая сборка Blocks без патча 04): ведомый
    видит только относительные величины, и цепочка «по соседу» неустойчива — колонна
    расползается. air_drag добавляет сопротивление как физику мира — колонна снова идёт."""
    assert len(_run_low_drag(0.0).finished) < 7
    result = _run_low_drag(0.33)
    assert not result.collided
    assert len(result.finished) == 7


def _fly_at_wall(mode: str, v0: float = 4.0, gap: float = 6.0, lag_steps: int = 2) -> float:
    """Ведомый летит к стене со скоростью v0, сосед впереди пропадает из кадра.
    Модель дрона: наклон с запаздыванием (команда исполняется через lag_steps кадров,
    наклон доходит за 0.4 с), сопротивление воздуха 0.35 1/с. Возвращает, насколько
    близко дрон подошёл к стене (отрицательно — врезался)."""
    import math
    from collections import deque
    from multiuav.control import G, rc_to_setpoint
    sc = Scenario.load(ROOT / "scenarios" / "line_blocks_city.yaml")
    world = str(ROOT / "scenarios" / "line_blocks_city.yaml")
    alg = CornerFollow(1, {"world_from": world, "walls": True, "wall_mode": mode}, sc.limits)
    wall = 65.0                                   # дом north 65..135 поперёк улицы
    pos, vel, tilt = np.array([wall - gap, 40.0]), np.array([v0, 0.0]), np.zeros(2)
    queue, closest, dt = deque(), 99.0, 0.1
    for k in range(250):
        me = DroneState(1, np.array([pos[0], pos[1], -30.0]), np.r_[vel, 0.0], 0.0)
        seen = [DroneState(2, np.array([pos[0] + 3, pos[1], -30.0]), np.zeros(3), 0.0)] if k < 3 else []
        queue.append(alg.step(me, seen, None, k * dt))
        if len(queue) > lag_steps:
            sp = rc_to_setpoint(queue.popleft(), sc.limits)
            tilt += (np.array([sp.roll, sp.pitch]) - tilt) * (dt / 0.4)
        acc = G * np.tan(tilt[::-1])              # вперёд (север), вправо (восток)
        vel += (acc - 0.35 * vel) * dt
        pos += vel * dt
        closest = min(closest, wall - pos[0], pos[0] - 55.0)   # улица north 55..65
    return closest


def test_rangefinder_brakes_by_range_rate_when_neighbour_lost():
    """С дальномером дрон тормозит перед стеной по скорости сближения с ней (стена
    неподвижна — это видно по дальномеру, своя скорость не нужна), и это работает,
    даже когда соседа не видно. Старый закон (push, отталкивание по расстоянию) с
    запаздыванием команд раскачивал дрон между стенами улицы, и он врезался."""
    assert _fly_at_wall("push") < 0.0
    assert _fly_at_wall("rate") > 0.5
