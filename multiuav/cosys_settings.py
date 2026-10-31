"""settings.json для Cosys-AirSim, собранный из сценария."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .backends.cosys import LIDAR_NAME, vehicle_name
from .scenario import Scenario

DEFAULT_PATH = Path.home() / "Documents" / "AirSim" / "settings.json"


def lidar_settings(scenario: Scenario) -> dict:
    """Круговой лидар: 5 лучей веером ±15° по вертикали, 360°, 10 оборотов в секунду.

    Один горизонтальный луч проскакивает над препятствием, верх которого чуть ниже
    дрона, — поэтому веер. Землю под дроном отсекает `lidar_to_ranges`.

    Остальные датчики (IMU, GPS, барометр, магнитометр) AirSim добавляет сам.
    """
    p = scenario.perception
    return {
        "SensorType": 6,
        "Enabled": True,
        "Range": p.lidar_range,
        "NumberOfChannels": 5,
        "VerticalFOVUpper": 15,
        "VerticalFOVLower": -15,
        "HorizontalFOVStart": 0,
        "HorizontalFOVEnd": 360,
        "RotationsPerSecond": 10,
        "MeasurementsPerCycle": 360,
        "X": 0, "Y": 0, "Z": -0.3,  # над винтами, иначе лучи упираются в свой корпус
        "DrawDebugPoints": False,
    }


def subwindows(scenario: Scenario) -> list[dict]:
    """Встроенные подокна AirSim внутри окна Unreal: камеры первых трёх ведомых.

    Больше трёх AirSim не умеет, и стоят они внизу экрана; клавиши 1, 2, 3 их
    прячут и показывают. Подписей и подсветки столкновений там нет — для этого
    `python -m multiuav cameras`.
    """
    return [{"WindowID": k, "CameraName": "front_center", "ImageType": 0,
             "VehicleName": vehicle_name(k + 1), "Visible": True}
            for k in range(min(3, scenario.drones - 1))]


def build_settings(scenario: Scenario, with_subwindows: bool = False,
                   messages: bool = False) -> dict:
    vehicles = {}
    for i, p in enumerate(scenario.start_positions()):
        vehicles[vehicle_name(i)] = {
            "VehicleType": "SimpleFlight",
            "X": round(float(p[0]), 2),
            "Y": round(float(p[1]), 2),
            "Z": 0,  # на земле под точкой старта; высоту набирает менеджер
            "Yaw": round(math.degrees(scenario.heading), 1),
        }
        if scenario.perception.lidar_range > 0:
            vehicles[vehicle_name(i)]["Sensors"] = {LIDAR_NAME: lidar_settings(scenario)}
        if scenario.model.startswith("fast"):
            # Своя доработка плагина (SimpleFlightQuadXParams/SimpleFlightApi): без неё
            # AirSim ключ Model для SimpleFlight молча игнорирует. fast2.5 → "Fast2.5": тяга x2.5.
            vehicles[vehicle_name(i)]["Model"] = "Fast" + scenario.model[4:]
        if scenario.rotor_drag > 0:
            # Тоже своя доработка плагина (патч 04): без неё ключ игнорируется.
            vehicles[vehicle_name(i)]["RotorDrag"] = scenario.rotor_drag
    settings = {
        "SettingsVersion": 2.0,
        "SimMode": "Multirotor",
        "ClockSpeed": 1,
        "ViewMode": "FlyWithMe",
        # Отладочные строки AirSim в углу экрана (и наши simPrintLogMessage) скрыты;
        # F2 в окне игры показывает/прячет их (своя доработка плагина, патч 05).
        "LogMessagesVisible": messages,
        "Vehicles": vehicles,
    }
    if with_subwindows:
        settings["SubWindows"] = subwindows(scenario)
    return settings


def write_settings(scenario: Scenario, path: Path = DEFAULT_PATH,
                   with_subwindows: bool = False, messages: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_settings(scenario, with_subwindows, messages), indent=2),
                    encoding="utf-8")
    return path
