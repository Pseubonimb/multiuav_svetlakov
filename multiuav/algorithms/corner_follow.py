"""«По повороту соседа»: ведомый без одометрии и без памяти пути.

Что знает ведомый (и больше ничего — это видно по коду ниже):
  * передняя камера (сектор fov_deg, дальность vision_range): расстояние до соседа,
    направление на него и куда сосед повёрнут (его курс относительно моего) — с ошибкой;
  * свой гироскоп — на сколько сам повернулся (угол, не путь), и высотомер;
  * СВОЕГО ПЕРЕМЕЩЕНИЯ И СВОЕЙ СКОРОСТИ НЕ ЗНАЕТ: одометрии нет, GPS нет. Поэтому
    и след соседа на карту не записать — ведомый решает по текущему кадру;
  * номеров соседей, их числа и того, кто ведущий, не знает. Связи по радио нет.
Аргумент leader, me.velocity и me.position (кроме как место, где стоит камера) не
используются.

Путь по одному кадру. Я лечу по пути (курс пути psi_p храню по гироскопу), сосед
тоже на пути и, уйдя за поворот, летит носом вдоль своей улицы. Простейший путь,
согласный с обоими, — ломаная «прямо от меня — поворот C — прямо к соседу», где C —
пересечение моей линии вперёд и линии соседа назад:
    a·(1, 0) + b·(cos φ, sin φ) = q,
q — где сосед в осях моего пути (вперёд, вправо), φ — его курс относительно пути;
a — сколько мне ещё до поворота, b — сколько сосед пролетел после него.
Поворот засчитывается, только когда сосед уже ушёл с моей линии вбок (|q_y| > side_min)
и повернулся заметно (|φ| > straight_deg): один его нос мог повернуться и раньше.

Управление — всё по тому, что видно сейчас (и по скорости изменения этого от кадра
к кадру):
  прямой участок — сосед впереди на моём пути: курс пути плавно доворачиваю на
      соседа (tau_path), его уход вбок гашу креном; дистанция — по сближению;
  поворот — лечу прямо до C, тормозя. Квадрокоптер не машина: поворот носа не
      поворачивает скорость, поэтому к C подлетаю медленно. Свою скорость к C узнаю
      по тому, как быстро убывает a (точка C неподвижна) — это камера, не одометрия.
      За a_turn до C (дрон по инерции ещё долетает) путь переходит на линию соседа;
  дистанция — по пути до соседа s (на повороте s = a + b): сближаюсь не быстрее
      close_max м/с; ds/dt — скорость сближения по кадрам, своя скорость не нужна;
      сосед по прямой ближе d_min — тормозить;
  камера — всегда на соседа (look_at: neighbor), чтобы он не ушёл за край кадра.
Дальномер (walls: true, по умолчанию выключен) — отталкивание от стен; с ним ошибки,
накопившиеся вдоль колонны, перестают кончаться стеной.
Сосед пропал (за углом дома или упал): lost_hold с поворачиваю камеру туда, где он был;
не нашёлся — зависаю и жду. Ползти вперёд вслепую (creep_s > 0, наклон creep_deg) можно,
но без дальномера это риск упереться в стену: в прогонах с ошибкой камеры 5 % такое
«доползание» добавляло столкновений, поэтому без дальномера по умолчанию выключено.

Параметры (followers: params):
  distance (6), d_min (3) — интервал по пути и наименьшее расстояние по прямой, м;
  fov_deg (140), vision_range (40), sector_deg (60) — камера и кого считать «впереди»;
  heading_noise_deg (3), tau_heading (0.3) — ошибка курса соседа по камере и её сглаживание;
  straight_deg (15), side_min (1) — когда считать, что сосед повернул;
  a_brake (1.5), a_stop (0.3), a_turn (1.75), k_v (1.5) — торможение и поворот;
  k_s (0.5), close_max (2) — дистанция; k_lat (0.4), k_dlat (1.0), tau_path (1) — вбок;
  k_yaw (2), look_at (neighbor | path), bearing_max (50) — камера;
  lost_hold (2), creep_s (0; с walls — 15), creep_deg (2) — сосед пропал;
  walls (false), wall_safe (2.5), k_wall (4) — НЕОБЯЗАТЕЛЬНО: если на дроне разрешён
        круговой дальномер (лидар), он отталкивает от стен ближе wall_safe м. По умолчанию
        выключено: в постановке задачи дрон знает только, где соседи;
  wall_mode (rate | push), a_wall (3), wall_look (6), v_out (1), k_out (1) — как
        дальномер удерживает от стен (см. _walls); push — как в версии 4;
  clock (sim) — sim: время по симулятору; auto: большее из модельного и настоящего (Unreal);
  world_from, debug, seed — как у line_follow.
Подробности и результаты — docs/corner_follow.md.
"""

from __future__ import annotations

import math
import time

import numpy as np

from ..control import G
from ..types import DroneState, RcOverride, wrap_angle
from .base import FollowerAlgorithm
from .line_follow import DRONE_HALF_WIDTH, _buildings, _seg_hits_box

#: Отметка дальше этого от прошлого положения соседа (в своих осях) — другой дрон, м.
GATE = 2.5


class CornerFollow(FollowerAlgorithm):
    """Ведомый «по повороту соседа»: камера + гироскоп, без одометрии и памяти пути."""

    vision_only = True
    landing = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        P = self.param
        self.rng = np.random.default_rng([int(P("seed", 0)), self.drone_id, 7])
        self.prev: tuple[np.ndarray, float] | None = None   # сосед в своих осях, курс соседа
        self.seen_t = -1e9
        self.yaw_prev: float | None = None
        self.t_prev: float | None = None
        self.s_f: float | None = None       # фильтр расстояния по пути и его производной
        self.ds_f = 0.0
        self.e_f: float | None = None       # то же для отхода вбок
        self.a_f: float | None = None       # то же для расстояния до поворота
        self.da_f = 0.0
        self.de_f = 0.0
        self.alt0: float | None = None
        self.psi_p: float | None = None     # курс пути (по гироскопу)
        self.phi_f: float | None = None     # сглаженный курс соседа (по гироскопу)
        self.mode = "wait"

    # ---------- камера ----------
    def _camera(self, me: DroneState, others: list[DroneState]) -> list[tuple[np.ndarray, float]]:
        """Отметки камеры: (положение соседа в своих осях [вперёд, вправо], его курс
        относительно своего). Номер, скорость соседа и его положение на карте
        отбрасываются здесь же; истинное me — только как место, где стоит камера."""
        P = self.param
        rng_max = float(P("vision_range", 40.0))
        half_fov = math.radians(float(P("fov_deg", 140.0))) / 2
        sig_h = math.radians(float(P("heading_noise_deg", 3.0)))
        walls = _buildings(P("world_from", None))
        meas = []
        for s in others:
            if s.collided:
                continue
            if any(s.altitude < b.height and _seg_hits_box(me.position[:2], s.position[:2], b)
                   for b in walls):
                continue
            d = s.position[:2] - me.position[:2]
            dist = float(np.hypot(*d))
            bearing = wrap_angle(math.atan2(d[1], d[0]) - me.yaw)
            if 1e-6 < dist <= rng_max and abs(bearing) <= half_fov:
                phi = wrap_angle(s.yaw - me.yaw + self.rng.normal(0.0, sig_h))
                meas.append((dist, bearing, phi))
        meas.sort()
        out: list[tuple[np.ndarray, float]] = []
        kept: list[tuple[float, float]] = []
        for dist, bearing, phi in meas:   # ближний дрон заслоняет дальних за ним
            if any(abs(wrap_angle(bearing - b)) < math.atan2(DRONE_HALF_WIDTH, r) for r, b in kept):
                continue
            kept.append((dist, bearing))
            out.append((dist * np.array([math.cos(bearing), math.sin(bearing)]), phi))
        return out

    def _choose(self, marks: list[tuple[np.ndarray, float]], turned: float):
        """Сосед впереди: тот же, что на прошлом кадре (ближайший к прошлой отметке,
        повёрнутой на свой поворот по гироскопу), иначе ближайший в переднем секторе."""
        if self.prev is not None:
            c, s = math.cos(-turned), math.sin(-turned)
            q0 = np.array([[c, -s], [s, c]]) @ self.prev[0]
            near = min(marks, key=lambda m: float(np.linalg.norm(m[0] - q0)), default=None)
            if near is not None and np.linalg.norm(near[0] - q0) < GATE:
                return near
        if self.prev is not None and self.t_prev is not None and \
                self.t_prev - self.seen_t <= float(self.param("lost_hold", 2.0)):
            return None   # свой сосед пропал только что — на другого не переключаемся
        sector = math.radians(float(self.param("sector_deg", 60.0)))
        ahead = [m for m in marks if abs(math.atan2(m[0][1], m[0][0])) <= sector]
        return min(ahead, key=lambda m: float(np.linalg.norm(m[0])), default=None)

    # ---------- путь по одному кадру ----------
    def _geometry(self, q: np.ndarray, phi: float):
        """Ломаная «прямо — поворот C — к соседу» по одному кадру.
        Возвращает ('corner', a, b) или ('line', s, e): a — сколько мне до поворота,
        b — сколько сосед пролетел после него; s — расстояние до соседа вдоль его линии,
        e — мой отход вбок от его линии (вправо — плюс)."""
        # Поворот засчитываем, только когда сосед уже ушёл с моей линии вбок: один его
        # нос мог повернуться и раньше (дрон доворачивает камеру на своего соседа).
        if (abs(math.sin(phi)) > math.sin(math.radians(float(self.param("straight_deg", 15.0))))
                and abs(q[1]) > float(self.param("side_min", 1.0))):
            b = q[1] / math.sin(phi)
            a = q[0] - b * math.cos(phi)
            if b > 0.0 and a >= 0.0:                   # поворот впереди меня, сосед за ним
                return "corner", a, b
        return "line", float(q[0]), float(q[1])

    @staticmethod
    def _filter(x: float, xf: float | None, dxf: float, dt: float) -> tuple[float, float]:
        """α-β фильтр: сглаженное значение и скорость его изменения."""
        if xf is None:
            return x, 0.0
        r = dt / 0.05
        alpha, beta = min(0.8, 0.25 * r), min(0.5, 0.03 * r * r)
        pred = xf + dxf * dt
        err = x - pred
        return pred + alpha * err, dxf + beta / dt * err

    def _rc(self, acc_f: float, acc_r: float, yaw_rate: float, climb: float) -> RcOverride:
        lim = self.limits
        a_max = G * math.tan(lim.max_tilt)
        pitch = math.atan2(float(np.clip(acc_f, -a_max, a_max)), G) / lim.max_tilt
        roll = math.atan2(float(np.clip(acc_r, -a_max, a_max)), G) / lim.max_tilt
        yaw = float(np.clip(yaw_rate / lim.max_yaw_rate, -1.0, 1.0))
        return RcOverride.from_normalized(roll=roll, pitch=pitch, throttle=climb, yaw=yaw)

    # ---------- шаг ----------
    @staticmethod
    def _rot(v: np.ndarray, ang: float) -> np.ndarray:
        c, s = math.cos(ang), math.sin(ang)
        return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])

    def step(self, me: DroneState, others: list[DroneState], leader: DroneState | None,
             t: float) -> RcOverride:
        P = self.param
        dt = self._dt(t)
        turned = 0.0 if self.yaw_prev is None else wrap_angle(me.yaw - self.yaw_prev)  # гироскоп
        self.yaw_prev = me.yaw
        # Курс пути psi_p — куда я лечу — хранится в той же системе, что показания
        # гироскопа (накопленный поворот). Нос (камера) может смотреть в другую сторону —
        # на соседа: так сосед не уходит за край кадра на повороте.
        if self.psi_p is None:
            self.psi_p = me.yaw
        if self.alt0 is None:
            self.alt0 = me.altitude
        climb = (self.alt0 - me.altitude) / self.limits.max_climb      # высотомер
        if self.landing:
            return RcOverride.from_normalized(throttle=-1.0 / self.limits.max_climb)

        mark = self._choose(self._camera(me, others), turned)
        if mark is None:
            return self._lost(me, t, turned, climb)
        q, phi = mark
        self.prev, self.seen_t = (q, phi), t
        # Курс соседа по камере шумит от кадра к кадру: сглаживаем его. Сглаживаем в
        # системе гироскопа (свой курс + курс соседа относительно меня), чтобы мой
        # собственный поворот не размазывался фильтром.
        phi_abs = wrap_angle(me.yaw + phi)
        if self.phi_f is None:
            self.phi_f = phi_abs
        else:
            k = min(1.0, dt / float(P("tau_heading", 0.3)))
            self.phi_f = wrap_angle(self.phi_f + k * wrap_angle(phi_abs - self.phi_f))
        phi = wrap_angle(self.phi_f - me.yaw)

        k_yaw = float(P("k_yaw", 2.0))
        d0, d_min = float(P("distance", 6.0)), float(P("d_min", 3.0))
        delta = wrap_angle(me.yaw - self.psi_p)      # нос относительно пути
        qp, phip = self._rot(q, delta), wrap_angle(phi + delta)   # сосед в осях пути
        kind, x1, x2 = self._geometry(qp, phip)
        r = float(np.linalg.norm(q))

        if kind == "corner":
            a, b = x1, x2
            self.mode = "corner"
            self.e_f = None
            self.s_f, self.ds_f = self._filter(a + b, self.s_f, self.ds_f, dt)
            acc_x = self._spacing(d0)
            # Точка поворота C неподвижна, и я лечу на неё по пути, поэтому a убывает
            # с моей скоростью вдоль пути: её даёт камера, одометрия не нужна.
            self.a_f, self.da_f = self._filter(a, self.a_f, self.da_f, dt)
            w_hat = -self.da_f
            a_stop = float(P("a_stop", 0.3))
            w_des = math.sqrt(2.0 * float(P("a_brake", 1.5)) * max(self.a_f - a_stop, 0.0))
            acc_x = min(acc_x, float(P("k_v", 1.5)) * (w_des - w_hat))
            acc_y = 0.0
            if self.a_f <= float(P("a_turn", 1.75)):
                # дошли до поворота: путь дальше — по линии соседа
                self.psi_p = wrap_angle(self.psi_p + phip)
                self.a_f, self.s_f = None, None
        else:
            # прямой участок: сосед впереди на моём пути. Курс пути плавно доворачиваем
            # на соседа, а его уход вбок (и скорость ухода — по кадрам) гасим креном.
            self.mode = "line"
            self.a_f, self.da_f = None, 0.0
            self.s_f, self.ds_f = self._filter(float(qp[0]), self.s_f, self.ds_f, dt)
            acc_x = self._spacing(d0)
            self.e_f, self.de_f = self._filter(float(qp[1]), self.e_f, self.de_f, dt)
            acc_y = float(P("k_lat", 0.4)) * self.e_f + float(P("k_dlat", 1.0)) * self.de_f
            bearing_p = math.atan2(qp[1], qp[0])
            self.psi_p = wrap_angle(self.psi_p + min(1.0, dt / float(P("tau_path", 1.0))) * bearing_p)
        if r < d_min and self.ds_f < 0.3:            # слишком близко по прямой — тормозить
            acc_x = min(acc_x, -G * math.tan(self.limits.max_tilt) * (d_min - r) / d_min - 1.0)
        acc_body = self._rot(np.array([acc_x, acc_y]), -wrap_angle(me.yaw - self.psi_p))
        acc_body = acc_body + self._walls(me)
        yaw_rate = self._nose(me, q, k_yaw)
        return self._rc(float(acc_body[0]), float(acc_body[1]), yaw_rate, climb)

    def _nose(self, me: DroneState, q: np.ndarray, k_yaw: float) -> float:
        """Куда смотрит нос (камера). look_at: path — по пути: по носу соседи сзади
        восстанавливают мой путь; отворачиваем его к соседу, только если тот уходит
        к краю кадра (bearing_max), и ровно настолько. neighbor — всегда на соседа."""
        if str(self.param("look_at", "neighbor")) == "neighbor":
            return k_yaw * math.atan2(q[1], q[0])
        qp = self._rot(q, wrap_angle(me.yaw - self.psi_p))
        bearing_p = math.atan2(qp[1], qp[0])
        edge = math.radians(float(self.param("bearing_max", 50.0)))
        extra = bearing_p - math.copysign(edge, bearing_p) if abs(bearing_p) > edge else 0.0
        return k_yaw * wrap_angle(self.psi_p + extra - me.yaw)

    def _walls(self, me: DroneState) -> np.ndarray:
        """Опционально (walls: true): дальномер. Это знание «где стена относительно меня
        сейчас», не одометрия. Дальномер — лидар симулятора (me.ranges); если его нет
        (в Unreal лидары выключены, они тормозят сцену), он имитируется по домам из
        world_from — как и заслонение.

        wall_mode: rate (по умолчанию). Стена неподвижна, поэтому скорость изменения
        расстояния до неё ṙ — это моя скорость от стены (своя скорость не нужна, как
        −da/dt у поворота). Для каждой стены ближе wall_look м — ограничение снизу:
            ṙ ≥ ṙ_min(r) = −√(2·a_wall·(r − wall_safe))   при r ≥ wall_safe
                           min(v_out, k_out·(wall_safe − r))  при r < wall_safe,
        то есть к стене подлетать не быстрее, чем можно остановиться к wall_safe, а
        ближе — уходить от неё. Нарушено — ускорение от стены k_v·(ṙ_min − ṙ). Закон
        односторонний и по скорости, поэтому между двумя стенами не раскачивает.
        wall_mode: push (версия 4): стена ближе wall_safe толкает с силой
        k_wall·(wall_safe − r) — с запаздыванием Unreal это раскачивало дрон между стенами."""
        if not bool(self.param("walls", False)):
            return np.zeros(2)
        P = self.param
        r_safe = float(P("wall_safe", 2.5))
        rate_mode = str(P("wall_mode", "rate")) == "rate"
        r_look = max(r_safe, float(P("wall_look", 6.0))) if rate_mode else r_safe
        seen: dict = {}            # ключ стены -> (расстояние, направление на неё в мировых осях)
        if me.ranges is not None:
            n = len(me.ranges)
            for i, r in enumerate(me.ranges):
                if r < r_look:
                    ang = (i + 0.5) * 2 * math.pi / n
                    seen[("s", i)] = (float(r), np.array([math.cos(ang), math.sin(ang)]))
        else:
            p = me.position[:2]
            for k, b in enumerate(_buildings(P("world_from", None))):
                if me.altitude >= b.height:
                    continue
                near = np.array([np.clip(p[0], *b.north), np.clip(p[1], *b.east)])
                d = float(np.linalg.norm(near - p))
                if 1e-6 < d < r_look:
                    seen[("b", k)] = (d, (near - p) / d)
        if not rate_mode:
            dirs = [(r, u) for r, u in seen.values() if r < r_safe]
            if not dirs:
                return np.zeros(2)
            push = -sum((r_safe - r) * u for r, u in dirs)
            push = push / math.sqrt(len(dirs)) * float(P("k_wall", 4.0))
            return self._rot(push, -me.yaw)
        prev = getattr(self, "_wall_prev", {})
        t_now = self.t_prev if self.t_prev is not None else 0.0
        dt = max(1e-3, t_now - getattr(self, "_wall_t", t_now - 0.1))
        self._wall_prev, self._wall_t = {}, t_now
        a_wall, k_v = float(P("a_wall", 3.0)), float(P("k_v", 1.5))
        v_out, k_out = float(P("v_out", 1.0)), float(P("k_out", 1.0))
        push = np.zeros(2)
        for key, (r, u) in seen.items():
            if key not in prev:
                self._wall_prev[key] = (r, 0.0)
                continue
            r0, rate0 = prev[key]
            rate = rate0 + min(1.0, dt / 0.2) * ((r - r0) / dt - rate0)     # сглаженное ṙ
            self._wall_prev[key] = (r, rate)
            if r >= r_safe:
                rate_min = -math.sqrt(2.0 * a_wall * (r - r_safe))
            else:
                rate_min = min(v_out, k_out * (r_safe - r))
            if rate < rate_min:
                push = push - k_v * (rate_min - rate) * u                  # от стены
        return self._rot(push, -me.yaw)                 # в оси дрона

    def _spacing(self, d0: float) -> float:
        """Дистанция по пути -> ускорение вдоль пути. Сближаемся с соседом не быстрее
        close_max м/с (а значит, летим не быстрее соседа + close_max): своей скорости
        не знаем, но скорость сближения ds/dt видна по кадрам."""
        P = self.param
        closing_des = float(np.clip(float(P("k_s", 0.5)) * (self.s_f - d0),
                                    -float(P("close_max", 2.0)), float(P("close_max", 2.0))))
        return float(P("k_v", 1.5)) * (closing_des + self.ds_f)

    def _lost(self, me: DroneState, t: float, turned: float, climb: float) -> RcOverride:
        """Соседа не видно (скрылся за углом дома, за краем кадра или упал).
        Сначала lost_hold с: не разгоняясь, поворачиваем камеру туда, где он был
        на последнем кадре (поворот — по гироскопу). Не нашёлся — если creep_s > 0,
        ещё столько секунд потихоньку ползём вперёд по своему пути (наклон creep_deg),
        продолжая смотреть в ту сторону; потом (по умолчанию сразу) — зависаем и ждём."""
        P = self.param
        if self.prev is None:
            return RcOverride.from_normalized(throttle=climb)
        q = self._rot(self.prev[0], -turned)
        self.prev = (q, wrap_angle(self.prev[1] - turned))
        yaw_rate = float(P("k_yaw", 2.0)) * math.atan2(q[1], q[0])
        lost_for = t - self.seen_t
        if self.mode not in ("lost", "creep", "wait"):
            self._say(t, "сосед впереди пропал из кадра — поворачиваю камеру туда, где он был")
            self.mode = "lost"
        self.s_f, self.e_f, self.a_f, self.phi_f = None, None, None, None
        wall = self._walls(me)        # дальномер работает и без соседа (с walls: true)
        if lost_for <= float(P("lost_hold", 2.0)):
            return self._rc(float(wall[0]), float(wall[1]), yaw_rate, climb)
        # ползти вслепую безопасно только с дальномером: по умолчанию с ним — 15 с, без — 0
        creep_s = float(P("creep_s", 15.0 if bool(P("walls", False)) else 0.0))
        if lost_for <= float(P("lost_hold", 2.0)) + creep_s:
            if self.mode != "creep":
                self._say(t, "сосед не нашёлся — потихоньку лечу вперёд по пути")
            self.mode = "creep"
            acc = G * math.tan(math.radians(float(P("creep_deg", 2.0))))
            body = self._rot(np.array([acc, 0.0]), -wrap_angle(me.yaw - self.psi_p)) + wall
            return self._rc(float(body[0]), float(body[1]), yaw_rate, climb)
        if self.mode != "wait":
            self._say(t, "соседа нет — останавливаюсь и жду")
        self.mode = "wait"
        if np.any(wall):
            return self._rc(float(wall[0]), float(wall[1]), 0.0, climb)
        return RcOverride.from_normalized(throttle=climb)

    def _dt(self, t: float) -> float:
        """Шаг времени. clock: sim — по симулятору (упрощённый симулятор); auto — большее
        из модельного и настоящего прошедшего времени (Unreal: если цикл не успевает,
        модельное время отстаёт, и скорости сближения по кадрам были бы завышены).
        С debug: true раз в 10 с печатается частота цикла (только для разбора полёта)."""
        wall = time.monotonic()
        if self.t_prev is None:
            self.t_prev, self._w_prev, self._w_hz, self._n_hz = t, wall, wall, 0
            return 0.05
        d_sim, d_wall = t - self.t_prev, wall - self._w_prev
        self.t_prev, self._w_prev = t, wall
        d = max(d_sim, d_wall) if str(self.param("clock", "sim")) == "auto" else d_sim
        self._n_hz += 1
        if bool(self.param("debug", False)) and self.drone_id == 1 and wall - self._w_hz >= 10.0:
            hz = self._n_hz / (wall - self._w_hz)
            print(f"[corner_follow] t={t:6.1f} цикл управления {hz:.1f} Гц", flush=True)
            self._w_hz, self._n_hz = wall, 0
        return max(1e-3, min(0.5, d))

    def _say(self, t: float, text: str) -> None:
        if bool(self.param("debug", False)):
            print(f"[corner_follow] t={t:6.1f} дрон {self.drone_id}: {text}", flush=True)
