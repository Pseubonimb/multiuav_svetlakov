"""Алгоритмы без связи между дронами: только то, что дрон видит сам.

Каждый ведомый знает лишь:
  * соседей, которых заметили его камеры (см. multiuav/perception.py) — с ошибкой
    оценки и без тех, кого заслонило здание;
  * свой круговой дальномер (лидар) — расстояние до препятствий по секторам.
Ни траекторий, ни намерений ведущего он не получает: никакого общего «следа».

Препятствия обходятся по идее Vector Field Histogram (Borenstein, Koren, 1991):
из свободных по дальномеру направлений выбирается ближайшее к желаемому,
скорость ограничивается тем, сколько свободного места впереди.
"""

from __future__ import annotations

import math

import numpy as np

from ..perception import sector_of
from ..types import DroneState, RcOverride
from .base import FollowerAlgorithm


#: При посадке цель всегда на столько метров ниже текущей высоты — спуск около 1 м/с,
#: мягкое касание земли.
LAND_STEP = 1.0


class VisionAlgorithm(FollowerAlgorithm):
    """Общее для алгоритмов «по зрению»: память о цели и обход препятствий.

    Параметры обхода: safe — запас до препятствия по ходу движения, м (2);
    width — полуширина коридора, который должен быть свободен, м (1.5);
    lookahead — на сколько секунд вперёд проверять путь (1.2);
    separation — с какого расстояния расталкиваться с соседями, м (3);
    max_speed — м/с (8);
    min_altitude — ниже не опускаться, даже если ведущий сел, м (3). Ниже ~2,5 м
    лидар задевает землю и принимает её за стены вокруг — ведомый «слепнет».

    landing — общая команда группе «сесть» (клавиша L в терминале, см. manager.py):
    ведомый останавливается и садится вертикально; снова False — взлетает на свою высоту.
    """

    vision_only = True
    landing = False  # выставляет SwarmManager по клавише

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.last_target: np.ndarray | None = None  # где последний раз видели цель
        self.last_velocity = np.zeros(3)  # как она тогда двигалась
        self.last_seen = 0.0
        self.altitude: float | None = None

    # --- выбор цели -----------------------------------------------------------

    def visible_ahead(self, others: list[DroneState]) -> DroneState | None:
        """Ближайший по номеру видимый дрон впереди по колонне (у него номер меньше)."""
        ahead = [s for s in others if s.id < self.drone_id and not s.collided]
        return max(ahead, key=lambda s: s.id) if ahead else None

    def remember(self, target: DroneState | None, t: float) -> np.ndarray | None:
        """Точка, куда лететь: видимая цель или её прогноз.

        Пропавшую из виду цель ищем там, куда она летела: место, где её видели
        последним, плюс последняя замеченная скорость, умноженная на время с тех пор
        (не дольше max_predict секунд, по умолчанию 30).
        """
        if target is not None:
            self.last_target = target.position.copy()
            self.last_velocity = target.velocity.copy()
            self.last_seen = t
            self.altitude = target.altitude
            return self.last_target
        if self.last_target is None:
            return None
        lost = min(t - self.last_seen, float(self.param("max_predict", 30.0)))
        return self.last_target + self.last_velocity * lost

    # --- обход препятствий ----------------------------------------------------

    def avoid(self, me: DroneState, desired: np.ndarray, others: list[DroneState]) -> np.ndarray:
        """Желаемая горизонтальная скорость -> безопасная с учётом дальномера и соседей.

        Каждое направление проверяется «коридором» шириной 2 × width: препятствие
        мешает, только если оно впереди и ближе width к оси коридора. Из направлений,
        где свободно хотя бы на safe + скорость × lookahead метров, берётся ближайшее
        к желаемому; скорость ограничивается свободным местом впереди.
        """
        safe = float(self.param("safe", 2.0))
        width = float(self.param("width", 1.5))
        lookahead = float(self.param("lookahead", 1.2))
        max_speed = float(self.param("max_speed", 8.0))

        separation = self._separation(me, others)
        speed = min(float(np.linalg.norm(desired)), max_speed)
        if me.ranges is None or speed < 1e-3:
            base = desired if speed < 1e-3 else desired / np.linalg.norm(desired) * speed
            return base + separation

        ranges = me.ranges
        n = len(ranges)
        bearings = (np.arange(n) + 0.5) * (2 * math.pi / n)
        dirs = np.stack([np.cos(bearings), np.sin(bearings)], axis=1)
        limit = float(ranges.max())
        hits = ranges < limit - 1e-6  # где дальномер во что-то упёрся
        points = dirs[hits] * ranges[hits, None]
        if len(points):
            forward = dirs @ points.T
            lateral = np.abs(dirs[:, 0, None] * points[None, :, 1] - dirs[:, 1, None] * points[None, :, 0])
            in_way = (forward > 0) & (lateral < width)
            free = np.where(in_way, forward, np.inf).min(axis=1).clip(max=limit)
        else:
            free = np.full(n, limit)

        heading = math.atan2(desired[1], desired[0])
        need = safe + speed * lookahead
        cost = np.abs((bearings - heading + math.pi) % (2 * math.pi) - math.pi)
        ok = free > need
        best = int(np.argmin(np.where(ok, cost, np.inf))) if ok.any() else int(np.argmax(free))
        allowed = max(0.0, (free[best] - safe) / lookahead)
        velocity = dirs[best] * min(speed, allowed)

        # Слишком близко к стене — отталкиваемся от неё, что бы ни хотела цель.
        nearest = int(np.argmin(ranges))
        if ranges[nearest] < safe:
            velocity -= dirs[nearest] * (safe - ranges[nearest]) * 2.0
        # Расталкивание с соседями — поверх: у стены скорость к цели мала, а разойтись надо.
        return velocity + separation

    def _separation(self, me: DroneState, others: list[DroneState]) -> np.ndarray:
        radius = float(self.param("separation", 3.0))
        push = np.zeros(2)
        for s in others:
            delta = me.position[:2] - s.position[:2]
            dist = float(np.linalg.norm(delta))
            if 1e-3 < dist < radius:
                push += delta / dist * (radius - dist) * 1.5
        return push

    def command(self, me: DroneState, velocity: np.ndarray) -> RcOverride:
        if self.landing:
            return self.velocity.command(me, np.zeros(2), None, me.altitude - LAND_STEP)
        altitude = self.altitude if self.altitude is not None else me.altitude
        altitude = max(altitude, float(self.param("min_altitude", 3.0)))
        yaw = math.atan2(velocity[1], velocity[0]) if np.linalg.norm(velocity) > 0.5 else None
        return self.velocity.command(me, velocity, yaw, altitude)


class VisualFollow(VisionAlgorithm):
    """Догоняет того, кого видит впереди по колонне, и сам объезжает препятствия.

    Цель — ближайший видимый дрон с меньшим номером (обычно предыдущий). Если
    здание его заслонило — летит туда, где видел его последним, и ищет снова.
    Параметры: distance — дистанция до цели, м (6); gain — как резко догонять, 1/с (0.8);
    плюс параметры обхода VisionAlgorithm.
    """

    def step(self, me: DroneState, others: list[DroneState], leader: DroneState | None,
             t: float) -> RcOverride:
        target = self.visible_ahead(others)
        point = self.remember(target, t)
        if point is None:
            return self.command(me, np.zeros(2))  # никого не видно и не было — висим
        distance = float(self.param("distance", 6.0))
        gain = float(self.param("gain", 0.8))
        delta = point[:2] - me.position[:2]
        dist = float(np.linalg.norm(delta))
        if dist < 1e-3:
            return self.command(me, np.zeros(2))
        direction = delta / dist
        pace = target.velocity if target is not None else self.last_velocity
        closing = max(0.0, float(pace[:2] @ direction))
        speed = max(0.0, closing + gain * (dist - distance))
        return self.command(me, self.avoid(me, direction * speed, others))


class Flock(VisionAlgorithm):
    """Стая по Рейнольдсу (1987): разлёт, выравнивание, сплочённость — по видимым соседям,
    плюс стремление за видимым ведущим (или туда, где его видели) и обход препятствий.

    Параметры: radius — кого из видимых считать соседями по стае, м (15);
    w_align, w_cohesion, w_goal — веса правил (0.6, 0.2, 1.0);
    distance — на каком расстоянии держаться от цели, м (6 × номер дрона);
    плюс параметры обхода VisionAlgorithm.
    """

    def step(self, me: DroneState, others: list[DroneState], leader: DroneState | None,
             t: float) -> RcOverride:
        radius = float(self.param("radius", 15.0))
        near = [s for s in others if not s.collided
                and np.linalg.norm(s.position[:2] - me.position[:2]) < radius]

        # Цель — видимый ведущий; если его не видно — ближайший видимый впереди по колонне.
        target = leader or self.visible_ahead(others)
        point = self.remember(target, t)
        goal = np.zeros(2)
        if point is not None:
            gap = float(self.param("distance", 6.0))
            if target is not None and target.id == 0:
                gap *= self.drone_id
            delta = point[:2] - me.position[:2]
            dist = float(np.linalg.norm(delta))
            if dist > 1e-3:
                pace = float(np.linalg.norm((target.velocity if target is not None
                                             else self.last_velocity)[:2]))
                goal = delta / dist * max(0.0, pace + 0.8 * (dist - gap))

        align = np.zeros(2)
        cohesion = np.zeros(2)
        if near:
            align = np.mean([s.velocity[:2] for s in near], axis=0) - me.velocity[:2]
            cohesion = np.mean([s.position[:2] for s in near], axis=0) - me.position[:2]
        desired = (float(self.param("w_goal", 1.0)) * goal
                   + float(self.param("w_align", 0.6)) * align
                   + float(self.param("w_cohesion", 0.2)) * cohesion)
        return self.command(me, self.avoid(me, desired, others))


__all__ = ["Flock", "VisionAlgorithm", "VisualFollow", "sector_of"]
