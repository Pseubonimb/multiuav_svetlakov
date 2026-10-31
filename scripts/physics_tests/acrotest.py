"""Замер Acro у Drone0: отклик по угловой скорости, предельный поворот, торможение.
python acrotest.py [P] [I] [D]  — коэффициенты регулятора угловых скоростей (без них — как в плагине).
Каждый тест — со сброса, с высоты 50 м, команды 50 раз в секунду. Обычно запускается из run_model.sh.
Переменные: FAST_K — множитель тяги модели (газ висения ≈ 0.59 / FAST_K);
TESTS — список тестов через запятую: step, turn, brake, glide, top (по умолчанию step,turn,brake)."""
import math, sys, time
import numpy as np
import cosysairsim as airsim
from cosysairsim.utils import quaternion_to_euler_angles

V = "Drone0"
c = airsim.MultirotorClient(); c.confirmConnection()
GAINS = [float(x) for x in sys.argv[1:4]] if len(sys.argv) > 1 else None
DT = 0.02
import os
K = float(os.environ.get("FAST_K", "1"))  # множитель тяги модели: газ висения ≈ 0.59 / K

def kin():
    k = c.simGetGroundTruthKinematics(V)
    r, p, y = quaternion_to_euler_angles(k.orientation)
    v = np.array([k.linear_velocity.x_val, k.linear_velocity.y_val, k.linear_velocity.z_val])
    w = np.array([k.angular_velocity.x_val, k.angular_velocity.y_val, k.angular_velocity.z_val])
    return k.position.z_val, r, p, v, w

def start():
    c.reset(); time.sleep(1)
    c.enableApiControl(True, V); c.armDisarm(True, V)
    if GAINS:
        g = airsim.PIDGains(*GAINS)
        c.setAngleRateControllerGains(airsim.AngleRateControllerGains(g, g, g), vehicle_name=V)
    c.takeoffAsync(vehicle_name=V).join()
    z0 = kin()[0]
    c.moveToZAsync(z0 - 50, 6, vehicle_name=V).join(); c.hoverAsync(vehicle_name=V).join(); time.sleep(1.5)
    return z0

class Hold:
    """Свой Acro-«пилот»: держит крен/тангаж через угловые скорости, высоту — газом.
    base — газ висения, подстраивается интегралом всё время."""
    def __init__(self, Z, base=0.5): self.Z, self.base = Z, base
    def cmd(self, roll, pitch):
        z, r, p, v, w = kin()
        e = z - self.Z  # >0 — ниже цели
        self.base = max(0.05, min(1.0, self.base + 0.03 * e * DT))
        thr = (self.base + 0.15 * e + 0.25 * v[2]) / max(math.cos(r) * math.cos(p), 0.25)
        self.thr = max(0.0, min(1.0, thr))
        # знаки проверены: +rate по крену увеличивает крен из quaternion_to_euler_angles,
        # +rate по тангажу — уменьшает тангаж
        c.moveByAngleRatesThrottleAsync(6.0 * (roll - r), -6.0 * (pitch - p), 0, self.thr, 0.1, vehicle_name=V)
        return z, r, p, v, w

def loop(secs, f):
    t0 = time.time(); out = []
    while time.time() - t0 < secs:
        out.append((time.time() - t0, f())); time.sleep(DT)
    return out

def test_step():
    """Рывок стика: 300°/с по крену 0,3 с, затем 0. Время до 90 % скорости и как быстро останавливается."""
    z0 = start(); h = Hold(z0 - 50, 0.59 / K)
    loop(4.0, lambda: h.cmd(0, 0))
    thr = h.base
    R = math.radians(300)
    a = loop(0.3, lambda: (c.moveByAngleRatesThrottleAsync(R, 0, 0, thr, 0.1, vehicle_name=V), kin())[1])
    b = loop(0.6, lambda: (c.moveByAngleRatesThrottleAsync(0, 0, 0, thr, 0.1, vehicle_name=V), kin())[1])
    t90 = next((t for (t, k) in a if abs(k[4][0]) >= 0.9 * R), None)
    t_stop = next((t for (t, k) in b if abs(k[4][0]) < math.radians(30)), None)
    roll_rel = math.degrees(a[-1][1][1]); roll_end = math.degrees(b[-1][1][1])
    print(f"  рывок 300°/с: макс. {math.degrees(max(abs(k[4][0]) for t, k in a)):.0f}°/с, до 90 % — "
          f"{'не достиг' if t90 is None else f'{t90 * 1000:.0f} мс'}; стик в ноль на крене {abs(roll_rel):.0f}° → "
          f"остановка вращения за {'>600' if t_stop is None else f'{t_stop * 1000:.0f}'} мс, "
          f"докрутил {abs(roll_end) - abs(roll_rel):+.0f}°; газ висения {thr:.2f}", flush=True)
    return thr

def test_turn(thr0):
    """Поворот вектора скорости на 90°: летим 15 м/с по X, затем как можно резче — 15 м/с по Y.
    Наклон — в сторону нужного ускорения, но не больше, чем позволяет держать высоту тяга.
    Снос — сколько метров дрон ещё пролетел по X после начала поворота."""
    tilt = min(math.acos(1 / (1.7 * K * 0.85)), math.radians(70))
    z0 = start(); h = Hold(z0 - 50, thr0)
    loop(1.0, lambda: h.cmd(0, 0))
    t0 = time.time()
    while time.time() - t0 < 10 and abs(kin()[3][0]) < 15:
        h.cmd(0, math.radians(-30)); time.sleep(DT)
    sx = math.copysign(1, kin()[3][0])  # куда разгоняет отрицательный тангаж
    x0 = c.simGetGroundTruthKinematics(V).position.x_val
    t0 = time.time(); xmax = 0; done = None; hmin = 1e9
    while time.time() - t0 < 8:
        v = kin()[3]
        ax, ay = 4.0 * (0 - v[0]), 4.0 * (15 - v[1])  # нужное ускорение к цели (0, 15)
        a = math.hypot(ax, ay); amax = 9.81 * math.tan(tilt)
        if a > amax: ax, ay = ax * amax / a, ay * amax / a
        # тангаж -θ разгоняет по sx; крен +φ (правое крыло вниз) — по +Y
        pitch = -math.atan(ax * sx / 9.81); roll = math.atan(ay / 9.81 * math.cos(pitch))
        z, r, p, v, w = h.cmd(roll, pitch)
        k = c.simGetGroundTruthKinematics(V)
        xmax = max(xmax, (k.position.x_val - x0) * sx); hmin = min(hmin, z0 - z)
        if done is None and abs(v[0]) < 1.0 and v[1] > 13.5: done = time.time() - t0
        time.sleep(DT)
    print(f"  поворот 90° на 15 м/с (наклон до {math.degrees(tilt):.0f}°): "
          f"{'не успел за 8 с' if done is None else f'{done:.1f} с'}, снос вперёд {xmax:.0f} м, "
          f"скорость по Y {v[1]:.1f}, мин. высота {hmin:.0f} м", flush=True)

def test_brake(thr0):
    """Разгон до 20 м/с на 45° тангажа, торможение наклоном 60° назад до 0,5 м/с."""
    z0 = start(); h = Hold(z0 - 50, thr0)
    loop(1.0, lambda: h.cmd(0, 0))
    t0 = time.time()
    while time.time() - t0 < 10 and np.hypot(*kin()[3][:2]) < 20:
        h.cmd(0, math.radians(-45)); time.sleep(DT)
    t_acc = time.time() - t0
    t0 = time.time(); dist = 0
    while time.time() - t0 < 10:
        z, r, p, v, w = h.cmd(0, math.radians(60))
        s = np.hypot(*v[:2]); dist += s * DT
        if s < 0.5: break
        time.sleep(DT)
    print(f"  разгон 0→20 м/с (тангаж 45°): {t_acc:.1f} с; торможение 20→0 (60° назад): {time.time() - t0:.1f} с, "
          f"высота {z0 - kin()[0]:.0f} м", flush=True)

def test_glide(thr0):
    """Скольжение: 15 м/с по X, затем крен и тангаж в ноль (высоту держим) — путь и время до 2 м/с."""
    z0 = start(); h = Hold(z0 - 50, thr0)
    loop(1.0, lambda: h.cmd(0, 0))
    t0 = time.time()
    while time.time() - t0 < 10 and np.hypot(*kin()[3][:2]) < 15:
        h.cmd(0, math.radians(-30)); time.sleep(DT)
    x0 = c.simGetGroundTruthKinematics(V).position; t0 = time.time(); half = None; s = 15
    while time.time() - t0 < 15:
        z, r, p, v, w = h.cmd(0, 0); s = np.hypot(*v[:2])
        if half is None and s < 7.5: half = time.time() - t0
        if s < 2: break
        time.sleep(DT)
    x1 = c.simGetGroundTruthKinematics(V).position
    d = math.hypot(x1.x_val - x0.x_val, x1.y_val - x0.y_val)
    print(f"  скольжение с 15 м/с в горизонте: до 7,5 м/с — {'—' if half is None else f'{half:.1f} с'}, "
          f"до 2 м/с — {'не за 15 с' if s >= 2 else f'{time.time() - t0:.1f} с'}, путь {d:.0f} м", flush=True)

def test_top(thr0, deg=60):
    """Потолок скорости: тангаж deg 10 с с удержанием высоты."""
    z0 = start(); h = Hold(z0 - 50, thr0)
    loop(1.0, lambda: h.cmd(0, 0))
    tr = loop(10.0, lambda: h.cmd(0, math.radians(-deg)))
    sp = [np.hypot(*k[3][:2]) for t, k in tr]
    t20 = next((t for (t, k), s in zip(tr, sp) if s >= 20), None)
    print(f"  потолок на {deg}°: {sp[-1]:.1f} м/с за 10 с (через 5 с — {sp[len(sp) // 2]:.1f}), "
          f"до 20 м/с — {'—' if t20 is None else f'{t20:.1f} с'}, мин. высота {min(z0 - k[0] for t, k in tr):.0f} м", flush=True)

print(f"Коэффициенты: {GAINS or 'по умолчанию'}", flush=True)
TESTS = os.environ.get("TESTS", "step,turn,brake").split(",")
thr0 = test_step() if "step" in TESTS else 0.59 / K
for name in TESTS:
    if name != "step": globals()["test_" + name](thr0)
c.reset()
