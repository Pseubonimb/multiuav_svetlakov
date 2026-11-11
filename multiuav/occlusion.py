"""Затемнение «по времени до ворот» — управляет MultiUAV, а не Blueprint карты.

Для каждых ворот задано N мс (из меню, по воротам по кругу; 0 — без затемнения, «ловушка»).
Экран гаснет, когда по скорости и расстоянию до плоскости ворот прогноз «долечу через N мс»,
и не открывается, пока дрон не пролетит плоскость ворот (плюс запас margin_ms). Пилот
замедлился — затемнение тянется до пролёта, но не дольше cap_ms; отвернул (улетает от ворот
или в сторону) или нажал R — снимается сразу. В occlusion_events.csv — каждое затемнение:
заданное N, фактические включение и выключение, момент пролёта и сколько летел вслепую.

Как гаснет экран: в карте есть объём постобработки MU_Occlusion — куб 400 м, всегда
включённый, но «запаркованный» глубоко под картой. Затемнить — перенести его к дрону,
вернуть — обратно (simSetObjectPose). Это делает отдельный поток со своим RPC-клиентом,
чтобы включать и выключать в рассчитанный момент, а не на шаге цикла (50 мс).
"""

from __future__ import annotations

import threading
import time

import numpy as np

from .track import ATTEMPT_RADIUS_M, Track

#: Прогноз вперёд на столько, чтобы включить вовремя между шагами цикла (50 мс), с.
LOOKAHEAD_S = 0.06
#: Ниже этой скорости к воротам (вдоль их оси) прогноз не строим, м/с.
MIN_APPROACH_MS = 1.0
#: Отвернул: отошёл назад от ворот на столько от точки, где погасло, м.
AWAY_M = 5.0
EVENT_COLUMNS = ["n", "gate", "gate_name", "planned_ms", "t_on_ms", "t_off_ms", "actual_ms",
                 "t_cross_ms", "blind_before_gate_ms", "end"]


class Occluder:
    """Решает, когда гасить экран перед следующими воротами и когда открывать."""

    def __init__(self, track: Track, durations_ms: list[int], screen, margin_ms: int = 150,
                 cap_ms: int = 3000) -> None:
        self.track = track
        self.durations = durations_ms
        self.screen = screen  # .show(gate, planned_ms, at, cap_ms) / .release(at, t_cross_ms, end)
        self.margin_s = margin_ms / 1000
        self.cap_ms = cap_ms
        self.next = 0  # индекс ворот, к которым летим
        self.active = None  # (ворота, s при включении)
        self._prev = None

    def step(self, pos: np.ndarray, vel_ned: np.ndarray, now: float) -> None:
        if not self.track.gates:
            return
        vel = np.array([vel_ned[0], vel_ned[1], -vel_ned[2]])  # alt вверх
        if self._prev is not None:
            for gate in self.track.gates:
                a = gate.crossing(self._prev, pos)
                if a is None or np.linalg.norm(self._prev + a * (pos - self._prev) - gate.center) > ATTEMPT_RADIUS_M:
                    continue
                if self.active is not None and self.active[0] is gate:
                    t_cross = now - (1 - a) * 0.05  # между прошлым шагом и этим
                    self.screen.release(t_cross + self.margin_s, _wall_ms(t_cross), "пролёт ворот")
                    self.active = None
                self.next = (gate.index + 1) % len(self.track.gates)
        self._prev = pos.copy()

        if self.active is not None:
            gate, s_on = self.active
            s = float(np.dot(pos - gate.center, gate.forward))
            right, up = gate.offset(pos)
            if s < s_on - AWAY_M or abs(right) > 2 * ATTEMPT_RADIUS_M or abs(up) > 2 * ATTEMPT_RADIUS_M:
                self.screen.release(now, None, "отвернул")
                self.active = None
            return

        gate = self.track.gates[self.next]
        planned = self.durations[gate.index] if gate.index < len(self.durations) else 0
        if planned <= 0:
            return  # ловушка: экран не гаснет
        s = float(np.dot(pos - gate.center, gate.forward))  # < 0 — ещё до ворот
        approach = float(np.dot(vel, gate.forward))
        right, up = gate.offset(pos)
        if s >= 0 or approach < MIN_APPROACH_MS or max(abs(right), abs(up)) > ATTEMPT_RADIUS_M:
            return
        eta = -s / approach
        if eta <= planned / 1000 + LOOKAHEAD_S:
            self.screen.show(gate, planned, now + max(0.0, eta - planned / 1000), self.cap_ms)
            self.active = (gate, s)

    def forget(self, next_gate: int = 0) -> None:
        """Сброс к воротам (R): снять затемнение, лететь к воротам после тех, где стоим."""
        if self.active is not None:
            self.screen.release(time.monotonic(), None, "R")
            self.active = None
        self.next = next_gate % max(1, len(self.track.gates))
        self._prev = None


def _wall_ms(t_monotonic: float) -> int:
    """Момент по монотонным часам → время компьютера, мс (как pc_time_ms)."""
    return time.time_ns() // 1_000_000 + int((t_monotonic - time.monotonic()) * 1000)


class _Screen:
    """Общая часть: одно затемнение за раз, журнал, ожидание «открыть»."""

    def __init__(self, log) -> None:
        self.log = log
        self.n = 0
        self.error = ""
        self._lock = threading.Lock()
        self._job = None  # (gate, planned_ms, at, cap_ms)
        self._release = None  # (at, t_cross_ms, end)
        self._wake = threading.Event()

    def show(self, gate, planned_ms: int, at: float, cap_ms: int) -> None:
        with self._lock:
            self._job, self._release = (gate, planned_ms, at, cap_ms), None
        self._wake.set()

    def release(self, at: float, t_cross_ms: int | None, end: str) -> None:
        with self._lock:
            if self._release is None:
                self._release = (at, t_cross_ms, end)
        self._wake.set()

    def _record(self, gate, planned_ms, on_ms, off_ms, t_cross_ms, end) -> None:
        self.n += 1
        blind = "" if t_cross_ms is None else t_cross_ms - on_ms
        self.log.row([self.n, gate.index, gate.name, planned_ms, on_ms, off_ms, off_ms - on_ms,
                      "" if t_cross_ms is None else t_cross_ms, blind, end], sync=True)


class CosysScreen(_Screen):
    """Поток, переносящий объём затемнения к дрону и обратно."""

    def __init__(self, volume: str, park_ned: list[float], log, host: str = "127.0.0.1") -> None:
        super().__init__(log)
        self.volume = volume
        self.park = park_ned
        self.host = host
        self._stop = False
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(10.0)

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
        while not self._stop:
            self._wake.wait(0.1)
            self._wake.clear()
            with self._lock:
                job = self._job
                self._job = None
            if job is None:
                continue
            gate, planned_ms, at, cap_ms = job
            time.sleep(max(0.0, at - time.monotonic()))
            with self._lock:
                if self._release is not None:  # отвернул раньше, чем погасло, — не гасить
                    self._release = None
                    continue
            pos = self._drone()
            on_ms = time.time_ns() // 1_000_000
            self._move(pos)
            cap = time.monotonic() + cap_ms / 1000
            t_cross_ms, end = None, "предел"
            while time.monotonic() < cap and not self._stop:
                with self._lock:
                    rel = self._release
                if rel is not None:
                    r_at, t_cross_ms, end = rel
                    time.sleep(max(0.0, min(r_at, cap) - time.monotonic()))
                    break
                self._wake.wait(0.05)
                self._wake.clear()
                p = self._drone()  # дрон летит: не дать ему вылететь из куба
                if np.linalg.norm(np.subtract(p, pos)) > 100:
                    pos = p
                    self._move(pos)
            self._move(self.park)
            off_ms = time.time_ns() // 1_000_000
            with self._lock:
                self._release = None
            self._record(gate, planned_ms, on_ms, off_ms, t_cross_ms, end)

    def _drone(self) -> list[float]:
        p = self.client.simGetVehiclePose("Drone0").position
        return [p.x_val, p.y_val, p.z_val]

    def _move(self, ned) -> None:
        a = self.airsim
        pose = a.Pose(a.Vector3r(float(ned[0]), float(ned[1]), float(ned[2])), a.Quaternionr())
        self.client.simSetObjectPose(self.volume, pose, True)

    def close(self) -> None:
        self.release(time.monotonic(), None, "конец попытки")
        time.sleep(0.2)
        self._stop = True
        self._wake.set()
        self._thread.join(timeout=5.0)


class LogScreen(_Screen):
    """Без Unreal (kinematic, тесты): только журнал, моменты — как запланированы."""

    def __init__(self, log) -> None:
        super().__init__(log)
        self._on = None

    def show(self, gate, planned_ms: int, at: float, cap_ms: int) -> None:
        self._on = (gate, planned_ms, _wall_ms(at), cap_ms)

    def release(self, at: float, t_cross_ms: int | None, end: str) -> None:
        if self._on is None:
            return
        gate, planned_ms, on_ms, cap_ms = self._on
        off_ms = min(_wall_ms(at), on_ms + cap_ms)
        self._record(gate, planned_ms, on_ms, off_ms, t_cross_ms, end)
        self._on = None

    def close(self) -> None:
        self.release(time.monotonic(), None, "конец попытки")
