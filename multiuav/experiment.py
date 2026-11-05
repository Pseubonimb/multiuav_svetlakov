"""Попытка эксперимента: параметры, папка, журнал полёта и сброс к воротам.

Папка попытки — runs/<код участника>/<ГГГГ-ММ-ДД_ЧЧ-ММ-СС>/. В ней:
  experiment.json  все параметры, начало и конец («2026-11-06, пятница, 16:05:01.123»)
  drone0.csv       траектория 20 раз/с (pc_time_ms — общие часы компьютера)
  collisions.csv   каждое касание: когда и с чем
  occlusion_events.csv  каждое затемнение: зона, когда включено и выключено
  resets.csv       возвраты к воротам по клавише R
  sticks.csv, buttons.csv, passport.json — запись пульта (sticks log)
  unreal.log       вывод игры; summary.json — итог
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .track import ATTEMPT_RADIUS_M, Track

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
DEFAULT_CONFIG = Path("experiments/friday.json")

#: Шапка drone0.csv — так её ждёт analyze.
FLIGHT_COLUMNS = ["pc_time_ms", "t_s", "north", "east", "alt_m", "yaw_deg", "roll_deg",
                  "pitch_deg", "collided", "rc_roll", "rc_pitch", "rc_throttle", "rc_yaw", "rc_mode"]
RESET_COLUMNS = ["pc_time_ms", "t_s", "gate", "gate_name", "source",
                 "from_north", "from_east", "from_alt", "to_north", "to_east", "to_alt"]
COLLISION_COLUMNS = ["pc_time_ms", "t_s", "object", "north", "east", "alt_m"]
#: occlusion_events.csv — каждое затемнение, как его включила программа (occlusion.py).
FSYNC_EVERY_S = 1.0


def local_time(ms: int | None = None) -> str:
    """Время компьютера в мс → «2026-11-06, пятница, 16:05:01.123» (местное)."""
    ms = time.time_ns() // 1_000_000 if ms is None else ms
    d = datetime.fromtimestamp(ms / 1000)
    return f"{d:%Y-%m-%d}, {WEEKDAYS[d.weekday()]}, {d:%H:%M:%S}.{ms % 1000:03d}"


def safe_name(text: str) -> str:
    """Код участника → имя папки: буквы, цифры, «-» и «_»; пробелы — «_»."""
    name = re.sub(r"[^\w\-]+", "_", text.strip(), flags=re.UNICODE).strip("._")
    return name[:60] or "без_кода"


def parse_occlusion(text: str | list) -> list[int]:
    """«75,100, 500 1000» или список → [75, 100, 500, 1000] (мс)."""
    if isinstance(text, (list, tuple)):
        return [int(round(float(v))) for v in text]
    parts = [p for p in re.split(r"[,;\s]+", str(text).strip()) if p]
    values = [int(round(float(p))) for p in parts]
    if any(v < 0 for v in values):
        raise ValueError("длительность затемнения не может быть отрицательной")
    return values


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    return apply_level(json.loads(Path(path).read_text(encoding="utf-8")))


def apply_level(cfg: dict, level: str | None = None) -> dict:
    """Уровень сложности (easy/medium/hard) → свои "map" и "track" из cfg["levels"]."""
    levels = cfg.get("levels") or {}
    level = level or cfg.get("level")
    if level and level in levels:
        cfg = {**cfg, "level": level, "map": levels[level]["map"], "track": levels[level]["track"]}
    return cfg


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # файл не окажется записанным наполовину


def new_attempt_dir(participant: str, root: Path = Path("runs")) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = root / safe_name(participant) / stamp
    out.mkdir(parents=True, exist_ok=False)
    return out


class _CsvLog:
    """CSV, который сразу уходит на диск: flush каждой строки, fsync раз в секунду."""

    def __init__(self, path: Path, columns: list[str]) -> None:
        self.f = open(path, "w", encoding="utf-8", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(columns)
        self.f.flush()
        self.rows = 0
        self._sync = time.monotonic()

    def row(self, values: list, sync: bool = False) -> None:
        self.w.writerow(values)
        self.f.flush()
        now = time.monotonic()
        if sync or now - self._sync >= FSYNC_EVERY_S:
            os.fsync(self.f.fileno())
            self._sync = now
        self.rows += 1

    def close(self) -> None:
        if not self.f.closed:
            self.f.flush()
            os.fsync(self.f.fileno())
            self.f.close()


class TrialRecorder:
    """step_hook для SwarmManager: журнал дрона 0, касания, сброс к последним воротам.

    reset_requests() — функция без аргументов, сколько раз попросили сброс (клавиша R,
    кнопка меню); duration_s — конец полёта по часам; on_tick(t_s) — раз в секунду.
    """

    def __init__(self, out: Path, track: Track | None, duration_s: float | None,
                 reset_requests=None, on_tick=None, alive=None, occluder=None) -> None:
        self.track = track
        self.duration_s = duration_s
        self.reset_requests = reset_requests
        self.on_tick = on_tick
        self.alive = alive
        self.occluder = occluder
        self.flight = _CsvLog(out / "drone0.csv", FLIGHT_COLUMNS)
        self.resets = _CsvLog(out / "resets.csv", RESET_COLUMNS)
        self.collisions = _CsvLog(out / "collisions.csv", COLLISION_COLUMNS)
        self.t0: float | None = None
        self.state_ms: int | None = None
        self.last_gate = None
        self.start_pose: tuple[np.ndarray, float] | None = None
        self._prev: np.ndarray | None = None
        self._next_tick = 0.0
        self.stopped_by = ""

    def stamp(self) -> None:
        """Вызвать прямо перед чтением состояний: это время и пойдёт в pc_time_ms."""
        self.state_ms = time.time_ns() // 1_000_000
        if self.t0 is None:
            self.t0 = time.monotonic()

    def t_s(self) -> float:
        return 0.0 if self.t0 is None else time.monotonic() - self.t0

    def __call__(self, manager, states, sent) -> None:
        me = states[0]
        t_s = self.t_s()
        ms = self.state_ms if self.state_ms is not None else time.time_ns() // 1_000_000
        pos = np.array([me.position[0], me.position[1], me.altitude])
        if self.start_pose is None:
            self.start_pose = (pos.copy(), me.yaw)
        if self.track is not None and self._prev is not None:
            # «Пройденные» ворота для R — любые, плоскость которых пересекли рядом с ними:
            # и сквозь проём, и над/мимо него (пилот 04.11 пролетал на 3 м выше центра).
            for gate in self.track.gates:
                a = gate.crossing(self._prev, pos)
                if a is not None and np.linalg.norm(self._prev + a * (pos - self._prev)
                                                    - gate.center) <= ATTEMPT_RADIUS_M:
                    self.last_gate = gate
        self._prev = pos
        if self.occluder is not None:
            self.occluder.step(pos, me.velocity, time.monotonic())
        rc = sent.get(0)
        channels = [rc.roll, rc.pitch, rc.throttle, rc.yaw, rc.mode] if rc else [""] * 5
        self.flight.row([ms, f"{t_s:.3f}", f"{pos[0]:.3f}", f"{pos[1]:.3f}", f"{pos[2]:.3f}",
                         f"{math.degrees(me.yaw):.2f}", f"{math.degrees(me.roll):.2f}",
                         f"{math.degrees(me.pitch):.2f}", int(me.touching), *channels])
        if me.touching:
            self.collisions.row([ms, f"{t_s:.3f}", me.touched, f"{pos[0]:.3f}", f"{pos[1]:.3f}",
                                 f"{pos[2]:.3f}"], sync=True)
        if self.reset_requests is not None:
            source = self.reset_requests()
            if source:
                self.reset(manager.backend, pos, source)
        if t_s >= self._next_tick:
            self._next_tick = math.floor(t_s) + 1.0
            if self.on_tick is not None:
                self.on_tick(t_s)
            if self.alive is not None and not self.alive():
                self.stopped_by = "игра закрылась или упала"
                manager.stop.set()
        if self.duration_s is not None and t_s >= self.duration_s:
            self.stopped_by = self.stopped_by or "время вышло"
            manager.stop.set()

    def reset(self, backend, pos: np.ndarray, source: str) -> None:
        """К центру последних пересечённых ворот (сквозь проём или рядом), носом по ходу
        трассы; ворот ещё не было — на старт."""
        if self.last_gate is not None:
            target, heading = self.last_gate.center.copy(), self.last_gate.heading
            gate, name = self.last_gate.index, self.last_gate.name
        else:
            target, heading = self.start_pose
            gate, name = -1, "старт"
        backend.reset(0, target, heading)
        self._prev = None  # скачок телепорта — не пролёт ворот
        if self.occluder is not None:
            self.occluder.forget()
        ms = time.time_ns() // 1_000_000
        self.resets.row([ms, f"{self.t_s():.3f}", gate, name, source,
                         *(f"{v:.3f}" for v in pos), *(f"{v:.3f}" for v in target)], sync=True)
        backend.message(f"RESET -> {name}")

    def close(self) -> None:
        for log in (self.flight, self.resets, self.collisions):
            log.close()
