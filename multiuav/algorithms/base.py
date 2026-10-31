"""Интерфейс алгоритма управления дроном."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..control import ControlLimits, VelocityController
from ..types import DroneState, RcOverride


class FollowerAlgorithm(ABC):
    """Алгоритм, который управляет одним дроном.

    На каждый дрон создаётся свой экземпляр, поэтому в нём можно хранить
    память (например, след предыдущего дрона). Дроны пронумерованы с нуля,
    дрон 0 — ведущий, которым управляет оператор.
    """

    def __init__(self, drone_id: int, params: dict[str, Any] | None = None,
                 limits: ControlLimits | None = None) -> None:
        self.drone_id = drone_id
        self.params = dict(params or {})
        self.limits = limits or ControlLimits()
        self.velocity = VelocityController(self.limits)

    @abstractmethod
    def step(self, me: DroneState, others: list[DroneState], leader: DroneState,
             t: float) -> RcOverride:
        """Вызывается с частотой цикла управления.

        me — свой дрон, others — все остальные дроны группы, leader — ведущий,
        t — время с начала сценария, с. Возвращает команду в стиле RC Override.
        """

    def param(self, name: str, default: Any) -> Any:
        return self.params.get(name, default)

    @staticmethod
    def find(others: list[DroneState], drone_id: int) -> DroneState | None:
        for state in others:
            if state.id == drone_id:
                return state
        return None

    def predecessor(self, others: list[DroneState], leader: DroneState) -> DroneState:
        """Ближайший по номеру впереди идущий дрон, который ещё в строю."""
        ahead = [s for s in others if s.id < self.drone_id and not s.collided]
        return max(ahead, key=lambda s: s.id) if ahead else leader
