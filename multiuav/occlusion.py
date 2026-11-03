"""Затемнение экрана в зонах трассы — управляет MultiUAV, а не Blueprint карты.

В карте (tools/unreal/friday_track.py) есть объём постобработки MU_Occlusion: куб 400 м,
всегда включённый, но «запаркованный» глубоко под картой. Пока камера дрона внутри него,
экран залит цветом или картинкой. Затемнить — перенести объём к дрону, вернуть —
обратно на стоянку (simSetObjectPose).

Отдельный поток со своим RPC-клиентом: включает затемнение в предсказанный момент входа
в зону и выключает точно через заданные мс, не дожидаясь шага цикла управления (50 мс).
Каждое затемнение — строка в occlusion_events.csv с фактическими моментами.
"""

from __future__ import annotations

import queue
import threading
import time

import numpy as np

from .track import Track

#: На сколько секунд вперёд по скорости искать вход в зону (чуть больше шага цикла).
LOOKAHEAD_S = 0.06
EVENT_COLUMNS = ["n", "zone", "zone_name", "dur_ms", "t_on_ms", "t_off_ms", "actual_ms"]


class Occluder:
    """Решает, когда затемнять: вход дрона в зону → длительность зоны (по кругу)."""

    def __init__(self, track: Track, durations_ms: list[int], screen) -> None:
        self.track = track
        self.durations = durations_ms
        self.screen = screen  # .show(zone, dur_ms, at_monotonic)
        self._inside = [False] * len(track.zones)

    def step(self, pos: np.ndarray, vel_ned: np.ndarray, now: float) -> None:
        vel = np.array([vel_ned[0], vel_ned[1], -vel_ned[2]])  # alt вверх
        ahead = pos + vel * LOOKAHEAD_S
        for z in self.track.zones:
            if self._inside[z.index]:
                self._inside[z.index] = z.contains(pos) or z.contains(ahead)
                continue
            a = 0.0 if z.contains(pos) else z.entry(pos, ahead)
            if a is None:
                continue
            self._inside[z.index] = True  # следующий вход — только после выхода из зоны
            dur = self.durations[z.index] if z.index < len(self.durations) else 0
            if dur > 0:
                self.screen.show(z, dur, now + a * LOOKAHEAD_S)

    def forget(self) -> None:
        """После сброса к воротам: зоны заново, как будто дрон в них ещё не был."""
        self._inside = [False] * len(self.track.zones)


class CosysScreen:
    """Поток, двигающий объём затемнения; журнал — в log (_CsvLog)."""

    def __init__(self, volume: str, park_ned: list[float], log, host: str = "127.0.0.1") -> None:
        self.volume = volume
        self.park = park_ned
        self.log = log
        self.host = host
        self.where = None  # функция → текущее положение дрона NED (north, east, down)
        self.n = 0
        self.error = ""
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(10.0)

    def show(self, zone, dur_ms: int, at: float) -> None:
        self._jobs.put((zone, dur_ms, at))

    def _run(self) -> None:
        import asyncio

        import cosysairsim as airsim

        asyncio.set_event_loop(asyncio.new_event_loop())  # клиенту AirSim нужен свой цикл
        self.airsim = airsim
        try:
            self.client = airsim.MultirotorClient(ip=self.host)
            self.client.confirmConnection()
            self._move(self.park)
        except Exception as e:
            self.error = str(e)
        self._ready.set()
        while True:
            job = self._jobs.get()
            if job is None:
                return
            zone, dur_ms, at = job
            time.sleep(max(0.0, at - time.monotonic()))
            pos = self._drone()
            on_ms = time.time_ns() // 1_000_000
            self._move(pos)
            t_on = time.monotonic()
            # Пока темно, дрон летит: если он подходит к краю куба, подвинуть объём за ним.
            end = t_on + dur_ms / 1000
            while time.monotonic() < end - 0.005:
                time.sleep(min(0.1, max(0.0, end - time.monotonic() - 0.003)))
                if end - time.monotonic() > 0.05:
                    p = self._drone()
                    if np.linalg.norm(np.subtract(p, pos)) > 100:
                        pos = p
                        self._move(pos)
            self._move(self.park)
            off_ms = time.time_ns() // 1_000_000
            self.n += 1
            self.log.row([self.n, zone.index, zone.label, dur_ms, on_ms, off_ms, off_ms - on_ms],
                         sync=True)

    def _drone(self) -> list[float]:
        p = self.client.simGetVehiclePose("Drone0").position
        return [p.x_val, p.y_val, p.z_val]

    def _move(self, ned) -> None:
        a = self.airsim
        pose = a.Pose(a.Vector3r(float(ned[0]), float(ned[1]), float(ned[2])), a.Quaternionr())
        self.client.simSetObjectPose(self.volume, pose, True)

    def close(self) -> None:
        self._jobs.put(None)
        self._thread.join(timeout=5.0)


class LogScreen:
    """Без Unreal (kinematic): затемнения только в журнал, по плану."""

    def __init__(self, log) -> None:
        self.log = log
        self.n = 0
        self.error = ""

    def show(self, zone, dur_ms: int, at: float) -> None:
        on_ms = time.time_ns() // 1_000_000 + int(max(0.0, at - time.monotonic()) * 1000)
        self.n += 1
        self.log.row([self.n, zone.index, zone.label, dur_ms, on_ms, on_ms + dur_ms, dur_ms])

    def close(self) -> None:
        pass
