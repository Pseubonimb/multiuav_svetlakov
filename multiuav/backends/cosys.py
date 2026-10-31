"""Cosys-AirSim (Unreal Engine 5): реальная картинка и физика.

Перед запуском симулятор должен быть открыт, а в ~/Documents/AirSim/settings.json
лежать файл, созданный командой `python -m multiuav settings <сценарий>`:
имена и стартовые точки дронов берутся оттуда.
"""

from __future__ import annotations

import math
import time

import numpy as np

from ..control import rc_to_acro, rc_to_setpoint
from ..scenario import Scenario
from ..types import ACRO, DroneState, RcOverride
from .base import SimBackend


def vehicle_name(drone_id: int) -> str:
    return f"Drone{drone_id}"


#: Как часто обновлять строку с каналами RC на экране, с.
RC_DISPLAY_PERIOD = 0.2


def rc_display_text(rc: RcOverride) -> str:
    """Каналы в порядке AETR, как на пульте: CH1 крен, CH2 тангаж, CH3 газ, CH4 рысканье."""
    return (f"CH1 Roll {rc.roll}  CH2 Pitch {rc.pitch}  "
            f"CH3 Thr {rc.throttle}  CH4 Yaw {rc.yaw}  Mode {rc.mode.upper()}")


#: Не чаще раза в столько секунд сообщать о столкновениях одного дрона.
ALARM_PERIOD = 1.5
#: Касания ниже этой высоты над точкой старта — взлёт или посадка, а не авария.
GROUND_ALTITUDE = 0.5
#: Целевая высота AltHold не опускается ниже этого (NED, м от точки старта, z вниз).
GROUND_Z = 1.0

#: Имя лидара в settings.json у каждого дрона.
LIDAR_NAME = "Lidar"
#: Препятствием на пути считаем то, что не ниже дрона больше чем на LIDAR_BELOW метров
#: и не выше больше чем на LIDAR_ABOVE; остальное — земля под дроном или высокое небо.
LIDAR_BELOW = 2.0
LIDAR_ABOVE = 3.0
#: Ближе этого лидар попадает в корпус и винты своего же дрона — это не препятствие.
LIDAR_SELF = 1.0


def _yaw_from_quaternion(q) -> float:
    w, x, y, z = q.w_val, q.x_val, q.y_val, q.z_val
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _rotate(q, points: np.ndarray) -> np.ndarray:
    """Повернуть точки из осей лидара в мировые оси NED кватернионом q."""
    w, x, y, z = q.w_val, q.x_val, q.y_val, q.z_val
    r = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    return points @ r.T


def lidar_to_ranges(points: np.ndarray, orientation, perception) -> np.ndarray:
    """Облако точек лидара (оси лидара: x вперёд, y вправо, z вниз) -> дальность по секторам.

    Лучи, которые ни во что не попали, AirSim отдаёт точкой (0, 0, 0), а в полёте
    лучи задевают свой корпус и винты (до ~0,6 м) — и то и другое отбрасываем.
    """
    points = points[np.linalg.norm(points, axis=1) > LIDAR_SELF]
    world = _rotate(orientation, points)
    world = world[(world[:, 2] < LIDAR_BELOW) & (world[:, 2] > -LIDAR_ABOVE)]  # NED: z вниз
    result = np.full(perception.sectors, float(perception.lidar_range))
    if len(world) == 0:
        return result
    dist = np.hypot(world[:, 0], world[:, 1])
    bearing = np.arctan2(world[:, 1], world[:, 0]) % (2 * math.pi)
    sector = (bearing / (2 * math.pi) * perception.sectors).astype(int) % perception.sectors
    np.minimum.at(result, sector, dist)
    return result


class CosysBackend(SimBackend):
    realtime = True

    def __init__(self, host: str = "127.0.0.1", command_duration: float = 0.5) -> None:
        self.host = host
        # Сколько дрон выполняет последнюю команду, если новые перестали приходить.
        self.command_duration = command_duration

    def start(self, scenario: Scenario) -> None:
        import cosysairsim as airsim

        self.limits = scenario.limits
        self.show_rc = scenario.show_rc
        self._last_rc_display = -math.inf
        self.client = airsim.MultirotorClient(ip=self.host)
        self.client.confirmConnection()
        self.names = [vehicle_name(i) for i in range(scenario.drones)]
        missing = set(self.names) - set(self.client.listVehicles())
        if missing:
            raise RuntimeError(
                f"В симуляторе нет дронов {sorted(missing)}. Создай settings.json командой "
                "`python -m multiuav settings <сценарий>` и перезапусти симулятор.")

        # Позиции в AirSim отсчитываются от точки старта каждого дрона,
        # поэтому храним эти точки, чтобы перевести всё в общую систему.
        self.origins = [np.array([p[0], p[1], 0.0]) for p in scenario.start_positions()]
        self.z_target = [-scenario.altitude for _ in self.names]
        self._last_z = [-scenario.altitude for _ in self.names]
        self._last_send = [time.monotonic() for _ in self.names]
        self.perception = scenario.perception
        self._last_ranges: list[np.ndarray | None] = [None for _ in self.names]

        for name in self.names:
            self.client.enableApiControl(True, name)
            self.client.armDisarm(True, name)
            if scenario.rate_gains:
                g = airsim.PIDGains(*scenario.rate_gains)
                self.client.setAngleRateControllerGains(
                    airsim.AngleRateControllerGains(g, g, g), vehicle_name=name)
        for f in [self.client.takeoffAsync(vehicle_name=n) for n in self.names]:
            f.join()
        for f in [self.client.moveToZAsync(-scenario.altitude, 3.0, vehicle_name=n)
                  for n in self.names]:
            f.join()
        # Касания земли при взлёте не считаем столкновениями.
        self._collision_baseline = [self.client.simGetCollisionInfo(n).time_stamp
                                    for n in self.names]
        self._last_stamp = list(self._collision_baseline)
        self._last_alarm = [-math.inf for _ in self.names]
        self._vector = getattr(airsim, "Vector3r", None)

    def states(self) -> list[DroneState]:
        result = []
        for i, name in enumerate(self.names):
            k = self.client.simGetGroundTruthKinematics(name)
            pos = self.origins[i] + np.array([k.position.x_val, k.position.y_val, k.position.z_val])
            vel = np.array([k.linear_velocity.x_val, k.linear_velocity.y_val,
                            k.linear_velocity.z_val])
            info = self.client.simGetCollisionInfo(name)
            collided = info.has_collided and info.time_stamp > self._collision_baseline[i]
            if info.has_collided and info.time_stamp != self._last_stamp[i]:
                self._last_stamp[i] = info.time_stamp
                if k.position.z_val < -GROUND_ALTITUDE:  # на земле при взлёте — не авария
                    self._alarm(i, pos, info.object_name)
            self._last_z[i] = k.position.z_val
            yaw = _yaw_from_quaternion(k.orientation)
            result.append(DroneState(i, pos, vel, yaw, collided, self._ranges(i, name)))
        return result

    def message(self, text: str) -> None:
        try:
            self.client.simPrintLogMessage("MultiUAV: ", text, 1)  # одна строка, обновляется
        except Exception:
            pass

    def _alarm(self, i: int, pos: np.ndarray, what: str) -> None:
        """Показать столкновение в окне Unreal: красная строка в сообщениях AirSim
        (та же строка для дрона обновляется) и надпись в мире над самим дроном."""
        now = time.monotonic()
        if now - self._last_alarm[i] < ALARM_PERIOD:
            return  # одно касание стены даёт десятки событий подряд
        self._last_alarm[i] = now
        try:
            self.client.simPrintLogMessage(f"COLLISION Drone{i}: ", what, 2)  # 2 — красный
            if self._vector is None:
                return
            above = self._vector(float(pos[0]), float(pos[1]), float(pos[2]) - 1.5)
            self.client.simPlotStrings([f"COLLISION #{i}"], [above], 3.0,
                                       [1.0, 0.1, 0.1, 1.0], ALARM_PERIOD * 2)
        except Exception:
            pass  # подсветка — не повод прерывать полёт

    def _ranges(self, i: int, name: str) -> np.ndarray | None:
        """Круговой дальномер из лидара LIDAR_NAME (его добавляет `multiuav settings`)."""
        p = self.perception
        if p.lidar_range <= 0:
            return None
        try:
            data = self.client.getLidarData(LIDAR_NAME, vehicle_name=name)
        except Exception:
            return self._last_ranges[i]  # лидара нет в settings.json — старый файл
        points = np.asarray(getattr(data, "point_cloud", []), dtype=float)
        if points.size < 3:
            return self._last_ranges[i]  # новый оборот лидара ещё не готов
        self._last_ranges[i] = lidar_to_ranges(points.reshape(-1, 3), data.pose.orientation, p)
        return self._last_ranges[i]

    def send(self, drone_id: int, rc: RcOverride) -> None:
        now = time.monotonic()
        dt = min(now - self._last_send[drone_id], 0.5)
        self._last_send[drone_id] = now
        name = self.names[drone_id]
        if rc.mode == ACRO:
            # Acro: SimpleFlight держит только угловые скорости, тягу задаёт газ напрямую.
            # Оси те же, что у углов ниже, поэтому и знак рысканья тот же.
            acro = rc_to_acro(rc, self.limits)
            self.client.moveByAngleRatesThrottleAsync(
                acro.roll_rate, acro.pitch_rate, -acro.yaw_rate, acro.throttle,
                self.command_duration, vehicle_name=name)
            # При возврате в AltHold держим ту высоту, на которой оказались.
            self.z_target[drone_id] = self._last_z[drone_id]
        else:
            sp = rc_to_setpoint(rc, self.limits)
            # Режим удержания высоты: стик газа задаёт скорость набора, копим её в целевую высоту.
            # Не глубже GROUND_Z под точкой старта: иначе, пока дрон стоит на земле с газом
            # вниз, цель «уходит под землю», и потом он долго не может оторваться.
            self.z_target[drone_id] = min(self.z_target[drone_id] - sp.climb_rate * dt, GROUND_Z)
            # В клиенте Cosys положительный yaw_rate — поворот влево, у нас — вправо.
            self.client.moveByRollPitchYawrateZAsync(
                sp.roll, sp.pitch, -sp.yaw_rate, self.z_target[drone_id],
                self.command_duration, vehicle_name=name)
        if self.show_rc and drone_id == 0 and now - self._last_rc_display >= RC_DISPLAY_PERIOD:
            self._last_rc_display = now
            # Тот же первый аргумент — AirSim обновляет одну строку, а не добавляет новые.
            self.client.simPrintLogMessage("RC Drone0: ", rc_display_text(rc))

    def advance(self, dt: float) -> None:
        pass  # Unreal живёт в реальном времени, менеджер сам выдерживает темп.

    def close(self) -> None:
        for name in getattr(self, "names", []):
            try:
                self.client.hoverAsync(vehicle_name=name)
            except Exception:
                pass
