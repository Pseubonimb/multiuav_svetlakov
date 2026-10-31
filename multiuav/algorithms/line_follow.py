"""«По пути ведущего»: ведомые выстраиваются на пути, пройденном ведущим, и держат
расстояние ПО ЭТОМУ ПУТИ, а не по прямой — как робот при выходе на изолинию.

Что знает ведомый (и больше ничего — это видно по коду ниже):
  * своя скорость и угловая скорость (одометрия, с ошибкой масштаба, шумом и дрейфом
    гироскопа) — по ним он ведёт своё положение в собственной системе отсчёта
    от точки старта; GPS нет;
  * камера, смотрящая вперёд (сектор fov_deg, дальность vision_range): направление на
    соседей и расстояние до них. Соседей отбирает и зашумляет multiuav/perception.py
    (дальность, заслонение зданиями, ошибка perception.noise); здесь дополнительно
    оставляется только передний сектор и учитывается, что ближний дрон заслоняет
    дальнего;
  * номеров соседей, их числа и того, кто из них ведущий, ведомый не знает:
    из наблюдений берутся только направление и расстояние, номер отбрасывается сразу,
    а кадры одному соседу сопоставляет собственный трекер (ближайший к прогнозу).
    Аргумент leader и дальномер (me.ranges) не используются. Связи по радио нет.

Как это связано с выходом на изолинию:
  изолиния              -> путь ведущего. Его никто не знает заранее; ведомый записывает,
                           где пролетал сосед впереди (сосед сам на этом пути), и
                           сглаживает запись (скользящее среднее с поправкой на усадку);
  значение поля          -> delta, расстояние со знаком от себя до записанного пути;
  закон курса            -> S = sin(psi - psi_пути) + mu*arctg(delta/b),
                           u = -u_max * насыщение(S/eps) — скользящий режим с узким
                           пограничным слоем вместо знаковой функции;
  дуга по записи         -> L = sigma(сосед) - sigma(я): расстояние до соседа ПО ПУТИ;
  скорость               -> v = v_соседа + k*(L - distance), но не больше безопасной
                           скорости по модели Гиппса (до соседа по прямой не ближе d_min)
                           и не больше sqrt(a_turn / кривизна пути впереди).
Если сосед пропал из виду (скрылся за углом), ведомый не угадывает, куда тот полетел:
долетает по записи почти до места, где видел его последним, и оттуда видит снова.

Параметры (followers: params):
  distance (6)        — интервал между дронами по длине пути, м;
  fov_deg (140), vision_range (40) — передняя камера; sector_deg (60) — кого считать
                        «впереди»; max_predict (2), d_lost (1.5) — когда сосед пропал;
  ds_rec (0.5), w_smooth (9) — шаг записи пути и окно сглаживания;
  smooth (twice) — сглаживание с поправкой на усадку угла; mean — простое скользящее среднее;
  mu (0.5), b_chi (1.0), u_max (1.2), eps (0.3), t_lead (0.5), look (1.5) — закон курса;
  k_speed (1.0), max_speed (10), a_turn (4) — закон скорости;
  d_min (3), a_brake (2), tau_brake (1) — безопасная скорость по Гиппсу;
  k_vel (1.5) — регулятор «скорость -> стики» (для инерционного дрона в Unreal — 1.0);
  altitude (своя начальная), min_altitude (3);
  look_at (neighbor) — куда смотрит камера: neighbor — на соседа впереди, course — по курсу;
  debug (false) — печатать, когда сосед пропал из кадра и когда путь записывается заново;
  world_from — сценарий с домами: только чтобы имитировать заслонение соседа стеной там,
        где в симуляторе нет лидара (perception.lidar_range: 0); алгоритм карту не использует;
  clock (auto) — шаг времени: sim — по симулятору (упрощённый симулятор, серии прогонов),
        auto — большее из времени симулятора и настоящего (Unreal, где цикл может отставать);
  шумы своих датчиков: noise (true), odo_scale (0.02), odo_noise (0.02), gyro_drift (0.002),
        seed (0); ошибка камеры задаётся в сценарии: perception.noise.
Подробности — docs/line_follow.md.
"""

from __future__ import annotations

import math
import time

import numpy as np

from ..control import VelocityController
from ..types import DroneState, RcOverride, wrap_angle
from .base import FollowerAlgorithm

#: При посадке (клавиша L) цель на столько метров ниже текущей высоты.
LAND_STEP = 1.0
#: Полуширина дрона для заслонения в кадре, м.
DRONE_HALF_WIDTH = 0.6
#: Отметка дальше этого от прогноза — уже другой сосед (для трекера), м.
TRACK_GATE = 2.5


_WORLD: dict = {}


def _buildings(path):
    """Дома из сценария — только чтобы имитировать, что стена заслоняет соседа от камеры
    (если в симуляторе нет лидара, которым это проверяет perception.py). Сам алгоритм
    карту не использует."""
    if not path:
        return []
    if path not in _WORLD:
        from ..scenario import Scenario
        _WORLD[path] = Scenario.load(path).buildings
    return _WORLD[path]


def _seg_hits_box(a: np.ndarray, b: np.ndarray, box) -> bool:
    """Пересекает ли отрезок a-b (в плане) прямоугольник дома."""
    d = b - a
    t0, t1 = 0.0, 1.0
    for p, q in ((-d[0], a[0] - box.north[0]), (d[0], box.north[1] - a[0]),
                 (-d[1], a[1] - box.east[0]), (d[1], box.east[1] - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
    return True


def _rot(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


class _Track:
    """α-β фильтр одного соседа: положение и скорость по последовательным кадрам."""

    def __init__(self, z: np.ndarray, t: float) -> None:
        self.x, self.v, self.t, self.n = z.copy(), np.zeros(2), t, 1

    def predict(self, t: float) -> np.ndarray:
        return self.x + self.v * (t - self.t)

    def update(self, z: np.ndarray, t: float) -> None:
        dt = max(t - self.t, 1e-3)
        # Коэффициенты подобраны для шага 0,05 с; при другом шаге пересчитываются так,
        # чтобы фильтр реагировал на торможение соседа одинаково быстро (в секундах).
        r = dt / 0.05
        alpha, beta = min(0.8, 0.2 * r), min(0.5, 0.02 * r * r)
        pred = self.x + self.v * dt
        err = z - pred
        self.x = pred + alpha * err
        self.v = self.v + (beta / dt) * err
        self.t, self.n = t, self.n + 1


class LineFollow(FollowerAlgorithm):
    """Ведомый «по пути ведущего» — только своя одометрия и передняя камера."""

    vision_only = True  # SwarmManager передаёт только тех, кого видят камеры
    landing = False     # выставляет SwarmManager по клавише L

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        P = self.param
        # Генератор ошибок своих датчиков; номер дрона — только чтобы ошибки были разными.
        self.rng = np.random.default_rng([int(P("seed", 0)), self.drone_id])
        self.noisy = bool(P("noise", True))
        self.k_odo = 1.0 + (self.rng.normal(0, float(P("odo_scale", 0.02))) if self.noisy else 0.0)
        self.drift = float(P("gyro_drift", 0.002)) * self.rng.choice([-1, 1]) if self.noisy else 0.0
        self.velocity = VelocityController(self.limits, k_vel=float(P("k_vel", 1.5)))
        self.p_hat: np.ndarray | None = None   # своё положение в своей системе отсчёта
        self.th_hat = 0.0                      # свой курс в ней же
        self.v_body = np.zeros(2)
        self.tracks: list[_Track] = []
        self.raw: list[np.ndarray] = []        # записанные положения соседа впереди
        self.pred: tuple | None = None         # сосед впереди: (положение, скорость)
        self.pred_t = -1e9
        self.pred_dir: float | None = None     # куда сосед впереди летел, когда его видели
        self.alt0: float | None = None

    # ---------- свои датчики: одометрия ----------
    def _odometry(self, me: DroneState, t: float) -> None:
        """Счисление пути по своей скорости (в связанных осях) и гироскопу.
        Истинное состояние me используется только как источник показаний датчиков,
        к которым добавляются ошибки."""
        if self.p_hat is None:
            self.p_hat, self.prev = np.zeros(2), (me.yaw, t)
        dt = t - self.prev[1]
        dyaw = wrap_angle(me.yaw - self.prev[0])           # гироскоп
        self.prev = (me.yaw, t)
        v_body = _rot(-me.yaw) @ me.velocity[:2]            # датчик скорости
        if self.noisy:
            v_body = v_body * self.k_odo + self.rng.normal(0, float(self.param("odo_noise", 0.02)), 2)
        if bool(self.param("debug", False)) and self.drone_id == 1:
            self._check_clock(me, t, dt)
        self.th_hat = wrap_angle(self.th_hat + dyaw + self.drift * dt)
        self.p_hat = self.p_hat + _rot(self.th_hat) @ v_body * dt
        self.v_body = v_body

    # ---------- свои датчики: передняя камера ----------
    def _camera(self, me: DroneState, others: list[DroneState]) -> list[tuple[float, float]]:
        """Что даёт камера: (расстояние, направление относительно своего курса) на каждого
        видимого соседа. Номер, скорость и курс соседа отбрасываются здесь же."""
        rng_max = float(self.param("vision_range", 40.0))
        half_fov = math.radians(float(self.param("fov_deg", 140.0))) / 2
        meas = []
        walls = _buildings(self.param("world_from", None))
        for s in others:            # others — уже отобранные perception.py, с ошибкой
            if s.collided:
                continue
            if any(s.altitude < b.height and _seg_hits_box(me.position[:2], s.position[:2], b)
                   for b in walls):
                continue            # между нами дом — камера соседа не видит
            d = s.position[:2] - me.position[:2]
            dist = float(np.hypot(*d))
            bearing = wrap_angle(math.atan2(d[1], d[0]) - me.yaw)
            if 1e-6 < dist <= rng_max and abs(bearing) <= half_fov:
                meas.append((dist, bearing))
        # ближний дрон заслоняет дальних, оказавшихся за ним в кадре
        meas.sort()
        visible: list[tuple[float, float]] = []
        for dist, bearing in meas:
            if not any(abs(wrap_angle(bearing - b)) < math.atan2(DRONE_HALF_WIDTH, r)
                       for r, b in visible):
                visible.append((dist, bearing))
        return visible

    def _track(self, meas: list[tuple[float, float]], t: float) -> list[_Track]:
        """Перевод отметок камеры в свою систему отсчёта и сопоставление кадров:
        каждая отметка — ближайшему по прогнозу соседу (без номеров)."""
        pts = [self.p_hat + _rot(self.th_hat) @ (r * np.array([math.cos(b), math.sin(b)]))
               for r, b in meas]
        self.tracks = [tr for tr in self.tracks if t - tr.t <= 1.0]
        free = list(self.tracks)
        seen = []
        for z in sorted(pts, key=lambda p: float(np.linalg.norm(p - self.p_hat))):
            best = min(free, key=lambda tr: float(np.linalg.norm(tr.predict(t) - z)), default=None)
            if best is not None and np.linalg.norm(best.predict(t) - z) < TRACK_GATE:
                best.update(z, t)
                free.remove(best)
            else:
                best = _Track(z, t)
                self.tracks.append(best)
            seen.append(best)
        return seen

    # ---------- записанный путь ----------
    def _curve(self, head: np.ndarray) -> np.ndarray:
        """Сглаженная запись + текущее положение соседа (конец кривой)."""
        pts = np.array(self.raw)
        w = int(self.param("w_smooth", 9))
        if len(pts) >= 3 and w > 1:
            h = w // 2

            def mean(p):
                return np.array([p[max(0, i - h):i + h + 1].mean(axis=0) for i in range(len(p))])

            sm = mean(pts)
            if str(self.param("smooth", "twice")) == "twice":
                # Скользящее среднее «скругляет» угол внутрь, и у каждого следующего дрона
                # скругление добавлялось бы к предыдущему. Поправка на усадку (сглаживание
                # «дважды» по Тьюки: 2·S(p) − S(S(p))) убирает основную часть этого сдвига,
                # почти не теряя подавления шума.
                sm = 2 * sm - mean(sm)
            sm[0] = pts[0]
        else:
            sm = pts
        return np.vstack([sm, head[None, :]])

    @staticmethod
    def _project(curve: np.ndarray, x: np.ndarray):
        """Проекция точки на ломаную: дуговая координата, расстояние со знаком,
        полная длина, номер отрезка."""
        a, b = curve[:-1], curve[1:]
        ab = b - a
        seg = np.linalg.norm(ab, axis=1)
        ok = seg > 1e-9
        tpar = np.zeros(len(a))
        tpar[ok] = np.clip(np.einsum("ij,ij->i", x - a[ok], ab[ok]) / seg[ok] ** 2, 0, 1)
        proj = a + ab * tpar[:, None]
        dist = np.linalg.norm(x - proj, axis=1)
        dist[~ok] = np.inf
        k = int(np.argmin(dist))
        sig = np.concatenate([[0.0], np.cumsum(seg)])
        tan = ab[k] / seg[k]
        nrm = np.array([-tan[1], tan[0]])
        return sig[k] + tpar[k] * seg[k], float((x - proj[k]) @ nrm), sig[-1], k

    @staticmethod
    def _tangent_at(curve: np.ndarray, sig_q: float) -> np.ndarray:
        seg = np.diff(curve, axis=0)
        length = np.linalg.norm(seg, axis=1)
        ok = length > 1e-9
        seg, length = seg[ok], length[ok]
        sig = np.concatenate([[0.0], np.cumsum(length)])
        k = int(np.clip(np.searchsorted(sig, sig_q) - 1, 0, len(seg) - 1))
        return seg[k] / length[k]

    @staticmethod
    def _curvature_ahead(curve: np.ndarray, sig_me: float, span: float, chord: float = 2.0) -> float:
        """Кривизна записанного пути впереди: поворот касательной между точками,
        отстоящими на chord м по дуге, делённый на chord (шум записи так не мешает)."""
        seg = np.diff(curve, axis=0)
        sig = np.concatenate([[0.0], np.cumsum(np.linalg.norm(seg, axis=1))])
        if sig[-1] < 2 * chord:
            return 0.0
        grid = np.arange(max(0.0, sig_me), min(sig[-1], sig_me + span + 2 * chord), 0.5)
        k = int(round(chord / 0.5))
        if len(grid) <= 2 * k:
            return 0.0
        x = np.interp(grid, sig, curve[:, 0])
        y = np.interp(grid, sig, curve[:, 1])
        d1 = np.stack([x[k:-k] - x[:-2 * k], y[k:-k] - y[:-2 * k]], axis=1)
        d2 = np.stack([x[2 * k:] - x[k:-k], y[2 * k:] - y[k:-k]], axis=1)
        ang = np.abs(np.arctan2(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0],
                                np.einsum("ij,ij->i", d1, d2)))
        return float(ang.max() / chord)

    # ---------- время ----------
    def _clock(self, t: float) -> float:
        """Собственные часы. В Unreal симуляция идёт в реальном времени, а цикл управления
        может не успевать за rate_hz: тогда t отстаёт от настоящего, и одометрия с оценкой
        скоростей соседей врут во столько же раз. В режиме auto шаг — большее из
        модельного и реального прошедшего времени. В упрощённом симуляторе нужен
        clock: sim — он сам может считать медленнее реального времени."""
        mode = str(self.param("clock", "auto"))
        wall = time.monotonic()
        if not hasattr(self, "_clk"):
            self._clk, self._t0, self._w0 = float(t), float(t), wall
            return self._clk
        d_sim, d_wall = float(t) - self._t0, wall - self._w0
        self._t0, self._w0 = float(t), wall
        d = d_sim if mode == "sim" else d_wall if mode == "wall" else max(d_sim, d_wall)
        self._clk += min(max(d, 0.0), 0.5)
        return self._clk

    # ---------- шаг управления ----------
    def step(self, me: DroneState, others: list[DroneState], leader: DroneState | None,
             t: float) -> RcOverride:
        P = self.param
        t = self._clock(t)
        self._odometry(me, t)
        seen = self._track(self._camera(me, others), t)
        if self.landing:
            return self.velocity.command(me, np.zeros(2), None, me.altitude - LAND_STEP)

        d0 = float(P("distance", 6.0))
        sector = math.radians(float(P("sector_deg", 60.0)))

        def bearing_of(tr: _Track) -> float:
            d = tr.x - self.p_hat
            return abs(wrap_angle(math.atan2(d[1], d[0]) - self.th_hat))

        ahead = [tr for tr in seen if bearing_of(tr) <= sector]
        near = self._choose_ahead(ahead, seen, t)

        if near is not None:
            pos, vel = near.x.copy(), (near.v.copy() if near.n > 5 else np.zeros(2))
            if self.pred is not None:
                guess = self.pred[0] + self.pred[1] * (t - self.pred_t)
                if np.linalg.norm(pos - guess) > 3.0:
                    self.raw = []        # сменился сосед впереди: путь записываем заново
                    self._debug(t, "сосед впереди сменился или найден снова — путь записывается заново")
            self.pred, self.pred_t = (pos, vel), t
            if float(np.linalg.norm(vel)) > 0.5:
                self.pred_dir = math.atan2(vel[1], vel[0])   # куда сосед летел
            if not self.raw:
                self.raw = [self.p_hat.copy()]   # начало записи — там, где стою я
            stride = pos - self.raw[-1]
            step_len = float(np.linalg.norm(stride))
            if step_len >= float(P("ds_rec", 0.5)):
                # Точку добавляем, только если она не «назад»: из-за ошибки дальности
                # отметка соседа дрожит вдоль линии взгляда. «Назад» — против того, куда
                # сосед летит (по его скорости), а пока скорость не оценена — против
                # направления записи на последних метрах. Шаг больше 2 м дрожанием быть
                # не может и записывается всегда (иначе запись могла бы «застрять»).
                if float(np.linalg.norm(vel)) > 0.5:
                    back = float(stride @ vel) < 0
                else:
                    base = self.raw[-1] - self.raw[max(0, len(self.raw) - 5)]
                    back = float(np.linalg.norm(base)) > 1e-6 and float(stride @ base) < 0
                if not back or step_len > 2.0:
                    self.raw.append(pos.copy())
        if self.pred is None:   # никого не видно с самого начала: висим и осматриваемся
            return self._send(me, np.zeros(2), self.th_hat + 0.5)

        age = t - self.pred_t
        if age > 0.5 and not getattr(self, "_lost_said", False):
            self._lost_said = True
            self._debug(t, "сосед впереди пропал из кадра")
        elif age <= 0.5:
            self._lost_said = False
        head = self.pred[0].copy()
        v_pred = float(np.linalg.norm(self.pred[1]))
        d_target = d0
        if age > 0:
            # Соседа впереди не видно (например, скрылся за углом). Путь дальше точки,
            # где его видели последним, неизвестен — туда не летим наугад, а долетаем
            # по записи почти до неё: оттуда снова видно, куда он ушёл.
            v_pred *= max(0.0, 1.0 - age / float(P("max_predict", 2.0)))
            d_target = float(P("d_lost", 1.5))

        curve = self._curve(head)
        sig_me, delta, sig_end, k = self._project(curve, self.p_hat)
        if k > 10:   # хвост записи, оставшийся позади, больше не нужен
            self.raw = self.raw[max(0, min(len(self.raw) - 2, k - 5)):]
        L = sig_end - sig_me                    # расстояние до соседа ПО ПУТИ

        # скорость: держим дуговое расстояние до соседа впереди...
        v_cmd = float(np.clip(v_pred + float(P("k_speed", 1.0)) * (L - d_target),
                              0.0, float(P("max_speed", 10.0))))
        # ...но так, чтобы при резком торможении соседа остановиться не ближе d_min по
        # прямой (безопасная скорость по модели Гиппса из теории транспортных потоков):
        # v*tau + v^2/(2a) <= h + v_p^2/(2a)
        if near is not None:
            r_min = float(np.linalg.norm(near.x - self.p_hat))
            h = max(0.0, r_min - float(P("d_min", 3.0)))
            a_b, tau_b = float(P("a_brake", 2.0)), float(P("tau_brake", 1.0))
            u_dir = (near.x - self.p_hat) / max(r_min, 1e-6)
            v_p = max(0.0, float(near.v @ u_dir)) if near.n > 5 else 0.0
            v_safe = -a_b * tau_b + math.sqrt((a_b * tau_b) ** 2 + v_p * v_p + 2.0 * a_b * h)
            v_cmd = min(v_cmd, max(0.0, v_safe))
        # ...и не быстрее, чем позволяет кривизна пути впереди
        kappa = self._curvature_ahead(curve, sig_me, 1.5 * d0)
        if kappa > 1e-6:
            v_cmd = min(v_cmd, math.sqrt(float(P("a_turn", 4.0)) / kappa))

        # курс: скользящий режим, как при выходе на изолинию
        mu, b_chi, u_max = float(P("mu", 0.5)), float(P("b_chi", 1.0)), float(P("u_max", 1.2))
        eps, t_lead = float(P("eps", 0.3)), float(P("t_lead", 0.5))
        v_hat = _rot(self.th_hat) @ self.v_body
        # направление пути чуть впереди своей проекции — поворот учитывается заранее
        tan_ahead = self._tangent_at(curve, sig_me + float(P("look", 1.5)))
        psi_path = math.atan2(tan_ahead[1], tan_ahead[0])
        psi = math.atan2(v_hat[1], v_hat[0]) if np.linalg.norm(v_hat) > 0.5 else psi_path
        S = math.sin(wrap_angle(psi - psi_path)) + mu * math.atan(delta / b_chi)
        u = -u_max * float(np.clip(S / eps, -1.0, 1.0))
        course = wrap_angle(psi + u * t_lead) if v_cmd > 0.3 else psi_path
        # Камера смотрит на соседа впереди (квадрокоптер может лететь в одну сторону, а
        # смотреть в другую): так сосед не уходит из кадра на повороте, а курс корпуса не
        # «качается» вместе с поправками скользящего режима.
        yaw = course
        if str(P("look_at", "neighbor")) == "neighbor":
            to_pred = self.pred[0] - self.p_hat
            if age <= 0.5 and float(np.linalg.norm(to_pred)) > 1.0:
                yaw = math.atan2(to_pred[1], to_pred[0])
        if age > 0.5 and v_cmd <= 0.3 and self.pred_dir is not None:
            # Долетел до места, где соседа видели последним, а его всё нет: на крутом
            # повороте (больше ~90°) он ушёл за край кадра. Поворачиваемся туда, куда он
            # летел, — камера смотрит вперёд, и сосед снова попадает в кадр.
            yaw = self.pred_dir
        return self._send(me, v_cmd * np.array([math.cos(course), math.sin(course)]), yaw)

    def _choose_ahead(self, ahead: list[_Track], seen: list[_Track], t: float) -> _Track | None:
        """Кто сосед впереди.
        Пока он виден — тот же дрон, что и в прошлом кадре (ближайший к прогнозу, не
        дальше 3 м от него): иначе после крутого поворота (больше ~90°) соседом мог бы
        оказаться дрон, идущий следом за нами по прежней улице.
        Если соседа потеряли — его ищут в стороне, куда он летел (±45°).
        В остальных случаях — ближайший в переднем секторе."""
        if self.pred is not None:
            age = t - self.pred_t
            if age < 0.5:
                expect = self.pred[0] + self.pred[1] * age
                same = min(seen, key=lambda tr: float(np.linalg.norm(tr.x - expect)), default=None)
                if same is not None and float(np.linalg.norm(same.x - expect)) < 3.0:
                    return same
            elif self.pred_dir is not None:
                def toward(tr: _Track) -> bool:
                    d = tr.x - self.p_hat
                    return abs(wrap_angle(math.atan2(d[1], d[0]) - self.pred_dir)) <= math.radians(45)
                pool = [tr for tr in seen if toward(tr)]
                return min(pool, key=lambda tr: float(np.linalg.norm(tr.x - self.p_hat)), default=None)
        return min(ahead, key=lambda tr: float(np.linalg.norm(tr.x - self.p_hat)), default=None)

    def _check_clock(self, me: DroneState, t: float, dt: float) -> None:
        """Только для разбора полёта: совпадает ли время алгоритма со временем симулятора.
        Сравнивает пройденный путь (по истинному положению) с «скорость × шаг времени».
        Если симулятор не успевает (тяжёлая сцена), он идёт медленнее реального времени,
        и одометрия, считающая по часам, завышает пройденный путь."""
        if not hasattr(self, "_ck"):
            self._ck = [me.position[:2].copy(), 0.0, 0.0, t]
            return
        self._ck[1] += float(np.linalg.norm(me.position[:2] - self._ck[0]))
        self._ck[2] += float(np.linalg.norm(me.velocity[:2])) * dt
        self._ck[0] = me.position[:2].copy()
        if t - self._ck[3] >= 10.0:
            if self._ck[2] > 5.0:
                print(f"[line_follow] t={t:6.1f} время симулятора идёт со скоростью "
                      f"{self._ck[1] / self._ck[2]:.2f} от времени алгоритма", flush=True)
            self._ck[1:] = [0.0, 0.0, t]

    def _debug(self, t: float, text: str) -> None:
        """Сообщения для разбора полёта (параметр debug: true): что ведомый «думал»."""
        if bool(self.param("debug", False)):
            p = self.p_hat
            print(f"[line_follow] t={t:6.1f} дрон {self.drone_id}: {text} "
                  f"(по своей оценке в точке {p[0]:.1f}, {p[1]:.1f})", flush=True)

    def _send(self, me: DroneState, vel: np.ndarray, yaw: float) -> RcOverride:
        """Команда «скорость, курс, высота» -> стики. Регулятор получает оценку своего
        состояния по одометрии (в своей системе отсчёта), а не истинное."""
        if self.alt0 is None:   # высота — своя начальная (барометр), соседей не повторяем
            a = self.param("altitude", None)
            self.alt0 = float(a) if a is not None else me.altitude
        alt = max(self.alt0, float(self.param("min_altitude", 3.0)))
        v_hat = _rot(self.th_hat) @ self.v_body
        me_hat = DroneState(-1, np.array([*self.p_hat, -me.altitude]),
                            np.array([*v_hat, me.velocity[2]]), self.th_hat, me.collided)
        return self.velocity.command(me_hat, vel, yaw, alt)
