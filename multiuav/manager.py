"""Менеджер группы: цикл «прочитать состояния -> спросить алгоритмы -> отправить команды»."""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .algorithms import FollowerAlgorithm, load_algorithm
from .backends.base import SimBackend
from .keyboard import Keyboard
from .perception import Observer
from .scenario import Scenario
from .types import DroneState, RcOverride


#: Клавиша посадки ведомых: L, в русской раскладке — та же клавиша «Д».
LAND_KEYS = ("l", "L", "д", "Д")


@dataclass
class RunResult:
    scenario: str
    leader: str
    followers: str
    drones: int
    sim_time: float = 0.0
    finished: dict[int, float] = field(default_factory=dict)  # номер дрона -> время финиша, с
    collided: dict[int, float] = field(default_factory=dict)  # номер дрона -> время столкновения
    # t, drone, north, east, altitude, yaw, collided, rc_roll, rc_pitch, rc_throttle, rc_yaw, rc_mode
    # (каналы пустые, если в этот шаг дрону команда не отправлялась)
    track: list[tuple] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return len(self.finished) / self.drones if self.drones else 0.0

    @property
    def group_time(self) -> float | None:
        """Время, за которое финишировал последний дошедший дрон."""
        return max(self.finished.values()) if self.finished else None

    def summary(self) -> dict:
        return {
            "scenario": self.scenario,
            "leader": self.leader,
            "followers": self.followers,
            "drones": self.drones,
            "finished": len(self.finished),
            "collided": len(self.collided),
            "success_rate": round(self.success_rate, 3),
            "group_time_s": None if self.group_time is None else round(self.group_time, 2),
            "sim_time_s": round(self.sim_time, 2),
            "collisions": {str(k): round(v, 2) for k, v in sorted(self.collided.items())},
        }

    def save(self, directory: str | Path) -> Path:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "track.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["t", "drone", "north", "east", "altitude", "yaw", "collided",
                             "rc_roll", "rc_pitch", "rc_throttle", "rc_yaw", "rc_mode"])
            writer.writerows(self.track)
        with open(out / "summary.json", "w", encoding="utf-8") as f:
            json.dump(self.summary(), f, ensure_ascii=False, indent=2)
        return out


class SwarmManager:
    def __init__(self, scenario: Scenario, backend: SimBackend) -> None:
        self.scenario = scenario
        self.backend = backend
        leader_cls = load_algorithm(scenario.leader.name)
        follower_cls = load_algorithm(scenario.followers.name)
        self.algorithms: list[FollowerAlgorithm] = [
            leader_cls(0, scenario.leader.params, scenario.limits)]
        self.algorithms += [follower_cls(i, scenario.followers.params, scenario.limits)
                            for i in range(1, scenario.drones)]
        # Алгоритмы «только по зрению» получают лишь тех, кого видят сами.
        self.observers = {i: Observer(i, scenario.perception)
                          for i, alg in enumerate(self.algorithms)
                          if getattr(alg, "vision_only", False)}

    def run(self) -> RunResult:
        sc = self.scenario
        result = RunResult(sc.name, sc.leader.name, sc.followers.name, sc.drones)
        dt = 1.0 / sc.rate_hz
        t = 0.0
        self.backend.start(sc)
        next_tick = time.monotonic()
        keyboard = Keyboard().__enter__()
        if self.observers:
            print(f"Клавиша {LAND_KEYS[0].upper()} — посадить ведомых / поднять обратно. "
                  "Ctrl+C — закончить полёт.", flush=True)
        try:
            while sc.duration_s is None or t <= sc.duration_s:
                for key in keyboard.pressed():
                    if key in LAND_KEYS:
                        self.toggle_landing()
                states = self.backend.states()
                self._update_outcomes(result, states, t)
                crashed = len(result.collided) if sc.stop_on_collision else 0
                if len(result.finished) + crashed >= sc.drones:
                    self._record(result, states, t, {})
                    break
                leader = states[0]
                sent: dict[int, RcOverride] = {}
                for state in states:
                    if state.collided and sc.stop_on_collision:
                        continue
                    if state.id in self.observers:
                        others, seen_leader = self.observers[state.id].observe(state, states)
                        rc = self.algorithms[state.id].step(state, others, seen_leader, t)
                    else:
                        others = [s for s in states if s.id != state.id]
                        rc = self.algorithms[state.id].step(state, others, leader, t)
                    self.backend.send(state.id, rc)
                    sent[state.id] = rc
                self._record(result, states, t, sent)
                self.backend.advance(dt)
                t += dt
                if self.backend.realtime:
                    next_tick += dt
                    time.sleep(max(0.0, next_tick - time.monotonic()))
        except KeyboardInterrupt:
            pass  # Ctrl+C — штатное завершение: дроны зависают, журнал и сводка сохраняются
        finally:
            keyboard.__exit__(None, None, None)
            self.backend.close()
        result.sim_time = t
        return result

    def toggle_landing(self) -> None:
        """Посадить ведомых «по зрению» или поднять их обратно (клавиша L)."""
        self.landing = not getattr(self, "landing", False)
        for i in self.observers:
            self.algorithms[i].landing = self.landing
        text = "ведомые садятся" if self.landing else "ведомые взлетают"
        print(f"\n>> {text}", flush=True)
        self.backend.message("FOLLOWERS: LANDING" if self.landing else "FOLLOWERS: TAKEOFF")

    def _record(self, result: RunResult, states: list[DroneState], t: float,
                sent: dict[int, RcOverride]) -> None:
        for s in states:
            rc = sent.get(s.id)
            channels = (rc.roll, rc.pitch, rc.throttle, rc.yaw, rc.mode) if rc else ("",) * 5
            result.track.append((round(t, 3), s.id, round(float(s.position[0]), 3),
                                 round(float(s.position[1]), 3), round(s.altitude, 3),
                                 round(s.yaw, 4), s.collided, *channels))

    def _update_outcomes(self, result: RunResult, states: list[DroneState], t: float) -> None:
        finish = self.scenario.finish
        for s in states:
            if s.collided and s.id not in result.collided and s.id not in result.finished:
                result.collided[s.id] = t
            if (finish and not s.collided and s.id not in result.finished
                    and finish.reached(s.position)):
                result.finished[s.id] = t
