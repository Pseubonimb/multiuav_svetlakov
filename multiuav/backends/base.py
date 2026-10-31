"""Интерфейс симулятора, с которым работает менеджер группы."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..scenario import Scenario
from ..types import DroneState, RcOverride


class SimBackend(ABC):
    #: True — симулятор живёт в реальном времени, и менеджер должен ждать между шагами.
    realtime: bool = False

    @abstractmethod
    def start(self, scenario: Scenario) -> None:
        """Расставить дроны по стартовым точкам и поднять на высоту сценария."""

    @abstractmethod
    def states(self) -> list[DroneState]:
        """Истинные состояния всех дронов в мировой системе NED, по порядку номеров."""

    @abstractmethod
    def send(self, drone_id: int, rc: RcOverride) -> None:
        """Передать дрону команду в стиле RC Override."""

    @abstractmethod
    def advance(self, dt: float) -> None:
        """Продвинуть симуляцию на dt секунд (для симулятора реального времени — ничего)."""

    def message(self, text: str) -> None:
        """Показать оператору строку состояния в окне симулятора (если оно есть)."""

    def close(self) -> None:
        pass
