"""Проверка карты в игре: как Blueprint раздаёт -OcclusionMs по зонам и как выглядят ворота.

Игра уже запущена с -OcclusionMs=... (см. scripts/occlusion_probe.sh). Скрипт переносит
дрон в зоны в заданном порядке и по кадрам камеры (простой серый кадр — затемнение)
меряет длительность каждого затемнения; затем снимает каждые ворота спереди.

  python scripts/occlusion_probe.py --order 0,1,2,3,4,0,3 --out runs/probe
"""

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

import cosysairsim as airsim

p = argparse.ArgumentParser()
p.add_argument("--track", default="maps/FlyingExampleMap.json")
p.add_argument("--order", default="0,1,2,3,4,0")
p.add_argument("--out", default="runs/probe")
p.add_argument("--watch", type=float, default=2.5, help="сколько секунд смотреть после входа")
args = p.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
track = json.loads(Path(args.track).read_text(encoding="utf-8"))

c = airsim.MultirotorClient()
c.confirmConnection()
c.enableApiControl(True, "Drone0")
c.armDisarm(True, "Drone0")


def put(n, e, alt, heading_deg):
    s = airsim.KinematicsState()
    s.position = airsim.Vector3r(n, e, -alt)
    s.orientation = airsim.euler_to_quaternion(0, 0, math.radians(heading_deg))
    c.simSetKinematics(s, True, "Drone0")
    # висеть на месте: команда углов 0 и высота точки
    c.moveByRollPitchYawZAsync(0, 0, math.radians(heading_deg), -alt, 30, vehicle_name="Drone0")


def frame():
    r = c.simGetImages([airsim.ImageRequest("front_center", airsim.ImageType.Scene, False, False)],
                       "Drone0")[0]
    img = np.frombuffer(r.image_data_uint8, dtype=np.uint8).reshape(r.height, r.width, 3)
    return img


def save_png(img, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.imsave(path, img)


results = []
for k, zi in enumerate(int(v) for v in args.order.split(",")):
    z = track["zones"][zi]
    zc, h, hd = z["center"], z["half_size"], z["heading_deg"]
    back = (math.cos(math.radians(hd)), math.sin(math.radians(hd)))
    reach = h["north"] * abs(back[0]) + h["east"] * abs(back[1])
    put(zc["north"] - back[0] * (reach + 6), zc["east"] - back[1] * (reach + 6), 4.0, hd)  # перед зоной
    time.sleep(1.5)
    base = frame()
    put(zc["north"], zc["east"], 4.0, hd)  # внутрь зоны
    t0 = time.monotonic()
    samples = []
    while time.monotonic() - t0 < args.watch:
        img = frame()
        samples.append((time.monotonic() - t0, float(img.std()), float(img.mean())))
        if len(samples) == 3:
            save_png(img, out / f"zone_{k}_{zi}_inside.png")
    flat = [s for s in samples if s[1] < 3.0]  # однотонный кадр
    dur = (flat[-1][0] - flat[0][0]) if flat else 0.0
    gray = round(float(np.mean([s[2] for s in flat])), 1) if flat else None
    results.append({"entry": k, "zone": zi, "label": z["label"], "occluded_s": round(dur, 2),
                    "gray_mean": gray, "scene_mean": round(float(base.mean()), 1),
                    "frames": len(samples)})
    print(results[-1], flush=True)
    put(zc["north"] - back[0] * (reach + 6), zc["east"] - back[1] * (reach + 6), 4.0, hd)
    time.sleep(max(0.5, 3.0 - args.watch))

for g in track["gates"]:
    gc, hd = g["center"], g["heading_deg"]
    f = (math.cos(math.radians(hd)), math.sin(math.radians(hd)))
    put(gc["north"] - f[0] * 8, gc["east"] - f[1] * 8, gc["alt"], hd)
    time.sleep(1.0)
    save_png(frame(), out / f"gate_{g['index'] + 1}.png")
    print("снимок ворот", g["index"] + 1, flush=True)

(out / "probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
