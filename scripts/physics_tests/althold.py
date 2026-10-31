"""AltHold у Drone0 как в MultiUAV (moveByRollPitchYawrateZAsync 30 раз в секунду):
висение (качка), спуск на 10 м, подъём на 10 м, разгон на 32°."""
import math, time
import numpy as np
import cosysairsim as airsim
from cosysairsim.utils import quaternion_to_euler_angles

V = "Drone0"
c = airsim.MultirotorClient(); c.confirmConnection()
c.reset(); time.sleep(1); c.enableApiControl(True, V); c.armDisarm(True, V)
c.takeoffAsync(vehicle_name=V).join()
z0 = c.simGetGroundTruthKinematics(V).position.z_val

def fly(z, secs, pitch=0.0):
    out = []; t0 = time.time()
    while time.time() - t0 < secs:
        c.moveByRollPitchYawrateZAsync(0, pitch, 0, z, 0.1, vehicle_name=V)
        k = c.simGetGroundTruthKinematics(V)
        r, p, _ = quaternion_to_euler_angles(k.orientation)
        out.append((time.time() - t0, z0 - k.position.z_val, math.degrees(r), math.degrees(p),
                    math.hypot(k.linear_velocity.x_val, k.linear_velocity.y_val)))
        time.sleep(1 / 30)
    return np.array(out)

fly(z0 - 30, 10)
a = fly(z0 - 30, 5)
print(f"  висение 30 м: высота {a[:, 1].min():.1f}…{a[:, 1].max():.1f} м, качка крен/тангаж ±{np.abs(a[:, 2]).max():.1f}/±{np.abs(a[:, 3]).max():.1f}°", flush=True)
for target, label in ((20, "спуск 30→20 м"), (30, "подъём 20→30 м")):
    a = fly(z0 - target, 8)
    t = next((t for t, h, *_ in a if abs(h - target) < 0.5), None)
    over = 0.0 if t is None else float(np.abs(a[a[:, 0] >= t, 1] - target).max())
    print(f"  {label}: {'не дошёл за 8 с' if t is None else f'{t:.1f} с'}, конец {a[-1, 1]:.1f} м, "
          f"после цели отклонение до {over:.1f} м", flush=True)
a = fly(z0 - 30, 6, math.radians(-32))
print(f"  разгон на 32° 6 с: {a[-1, 4]:.1f} м/с, высота {a[:, 1].min():.1f}…{a[:, 1].max():.1f} м", flush=True)
c.reset()
